using SkiaSharp;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Model.Drawing;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.Logging;
using System;
using System.IO;
using System.Threading.Tasks;
using System.Collections.Generic;
using EmbyPluginSimpleUi.Common;
using MediaBrowser.Common;
using System.Globalization;
using System.Linq;
using MediaBrowser.Controller.Base;


//ToDo: Add JPG quality to options
namespace EmbyPluginUiDemo.meet
{
    public sealed class ImageEnhancer : CommonBase, IImageEnhancer
    {
        private readonly ILogger _logger;
        private readonly string _pluginVersion;
        private static readonly object _cacheLock = new object();

        MetadataProviderPriority IImageEnhancer.Priority => MetadataProviderPriority.First;

        public ImageEnhancer(ILogger logger, IApplicationHost appHost)
            : base(new ServiceRoot(appHost))
        {
            _logger = logger;
            _pluginVersion = typeof(ImageEnhancer).Assembly.GetName().Version?.ToString() ?? "0.0.0.0";
            try
            {
                var asm = typeof(SKBitmap).Assembly;
                _logger.Info($"ImageEnhancer v{_pluginVersion} | Skia: {asm.FullName}");
            }
            catch (Exception ex)
            {
                _logger.Error(ex + "Skia assembly probe failed");
            }
        }

        // ----- Cache-Key steuert Re-Render -----
        string IImageEnhancer.GetConfigurationCacheKey(BaseItem item, ImageType imageType)
        {
            var key = item.InternalId.ToString(CultureInfo.InvariantCulture);
            var imgTag = item.DateModified.Ticks.ToString();
            var itemPart = $"{item.Id}_{imageType}_{imgTag}";
            var targetFolder = Options?.TargetFolder;

            if (string.IsNullOrWhiteSpace(targetFolder))
                return $"{itemPart}_SkiaEnhanced_v{_pluginVersion}_NOOVERLAY";

            // Check auf Overlay (Cache oder Disk) - behandelt Updates und neue Dateien
            if (TryGetOverlayFromCacheOrDisk(targetFolder, key, out var ov))
            {
                return $"{itemPart}_SkiaEnhanced_v{_pluginVersion}_{ov.ModificationDate}";
            }

            return $"{itemPart}_SkiaEnhanced_v{_pluginVersion}_NOOVERLAY";
        }

        EnhancedImageInfo IImageEnhancer.GetEnhancedImageInfo(BaseItem item, string inputFile, ImageType imageType, int imageIndex)
            => new EnhancedImageInfo { RequiresTransparency = false };

        ImageSize IImageEnhancer.GetEnhancedImageSize(BaseItem item, ImageType imageType, int imageIndex, ImageSize originalImageSize)
        {
            if (imageType != ImageType.Primary) return originalImageSize;

            // Fixe Größen für Episoden basierend auf Aspect Ratio (Logic aus overlays.py)
            if (item.GetType().Name.Equals("Episode", StringComparison.OrdinalIgnoreCase))
            {
                double aspect = 0;
                //try
                //{
                //    aspect = item.GetDefaultPrimaryImageAspectRatio();
                //}
                //catch
                //{
                //}
                if (aspect <= 0 && originalImageSize.Height > 0)
                    aspect = (double)originalImageSize.Width / originalImageSize.Height;

                // 16:9 (approx 1.77) vs 4:3 (approx 1.33). Cutoff bei 1.5
                if (aspect > 1.5)
                    return new ImageSize { Width = 1920, Height = 1080 };
                else
                    return new ImageSize { Width = 1440, Height = 1080 };
            }

            return new ImageSize { Width = 1000, Height = 1500 };
        }

        bool IImageEnhancer.Supports(BaseItem item, ImageType imageType)
        {
            if (imageType != ImageType.Primary) return false;

            var targetFolder = Options?.TargetFolder;
            if (string.IsNullOrWhiteSpace(targetFolder) || !Directory.Exists(targetFolder))
                return false;

            BuildOverlayIndexIfNeeded(targetFolder);

            var key = item.InternalId.ToString(CultureInfo.InvariantCulture);
            if (TryGetOverlayFromCacheOrDisk(targetFolder, key, out var entry))
            {
                //_logger.Info($"Support check for item: {item.Name}, Supported: True - File: {entry.Path}");
                return true;
            }
            return false;
        }

        // ----- Render -----
        async Task IImageEnhancer.EnhanceImageAsync(BaseItem item, string inputFile, string outputFile, ImageType imageType, int imageIndex)
        {
            if (imageType != ImageType.Primary) return;

            var key = item.InternalId.ToString(CultureInfo.InvariantCulture);
            var targetFolder = Options?.TargetFolder;

            if (string.IsNullOrWhiteSpace(targetFolder)) return;

            if (Options?.qualified == null)
                BuildOverlayIndexIfNeeded(targetFolder);

            if (!TryGetOverlayFromCacheOrDisk(targetFolder, key, out var ov))
            {
//                _logger.Info($"Enhancer skipped: no overlay for {item.Name} ({key})");
                return;
            }

            await WaitForFileAccessAsync(ov.Path).ConfigureAwait(false);

            Directory.CreateDirectory(Path.GetDirectoryName(outputFile)!);

//            _logger.Info($"Enhancer start: {item.Name} | In: {inputFile} | Out: {outputFile} | Ov: {ov.Path}");

            using var inputStream = File.Open(inputFile, FileMode.Open, FileAccess.Read, FileShare.Read);
            using var overlayStream = File.Open(ov.Path, FileMode.Open, FileAccess.Read, FileShare.Read);

            using var bitmap = SKBitmap.Decode(inputStream);
            using var overlayBitmap = SKBitmap.Decode(overlayStream);

            if (bitmap is null)
            {
                _logger.Error($"Decode input failed: {inputFile}");
                if (File.Exists(outputFile)) File.Delete(outputFile);
                return;
            }
            if (overlayBitmap is null)
            {
                _logger.Error($"Decode overlay failed: {ov.Path}");
                var tmpFile2 = outputFile + ".tmp";
                try
                {
                    using (var sourceStream = File.Open(inputFile, FileMode.Open, FileAccess.Read, FileShare.Read))
                    using (var fs = File.Open(tmpFile2, FileMode.Create, FileAccess.Write, FileShare.None))
                    {
                        await sourceStream.CopyToAsync(fs).ConfigureAwait(false);
                    }
                    File.Move(tmpFile2, outputFile, true);
                }
                catch (Exception ex)
                {
                    _logger.Error($"Fallback copy failed: {ex.Message}");
                    try { if (File.Exists(tmpFile2)) File.Delete(tmpFile2); } catch { }
                    if (File.Exists(outputFile)) File.Delete(outputFile);
                }
                return;
            }

            // Use the overlay's dimensions as the target size
            var targetWidth = overlayBitmap.Width;
            var targetHeight = overlayBitmap.Height;

            using var resizedBitmap = ResizeOrCopy(bitmap, targetWidth, targetHeight);
            if (resizedBitmap is null) { _logger.Error("Resize failed"); return; }

            using var combinedBitmap = new SKBitmap(targetWidth, targetHeight);
            using var canvas = new SKCanvas(combinedBitmap);
            canvas.Clear(SKColors.Black);
            canvas.DrawBitmap(resizedBitmap, 0, 0);

            var overlayX = (targetWidth - overlayBitmap.Width) / 2;
            var overlayY = (targetHeight - overlayBitmap.Height) / 2;
            canvas.DrawBitmap(overlayBitmap, overlayX, overlayY);
            canvas.Flush();

            using var image = SKImage.FromBitmap(combinedBitmap);
            using var data = image.Encode(SKEncodedImageFormat.Jpeg, 85);

            if (data is null)
            {
                _logger.Error("Image encoding failed");
                return;
            }

            var tmpFile = outputFile + ".tmp";
            try
            {
                using (var outputStream = File.Open(tmpFile, FileMode.Create, FileAccess.Write, FileShare.None))
                {
                    data.SaveTo(outputStream);
                    await outputStream.FlushAsync().ConfigureAwait(false);
                }
                File.Move(tmpFile, outputFile, true);
            }
            catch (Exception ex)
            {
                _logger.Error($"Error saving image: {ex.Message}");
                try { if (File.Exists(tmpFile)) File.Delete(tmpFile); } catch { }
                throw;
            }

            _logger.Info($"Enhancer done: {item.Name} → {outputFile}");
        }

        // ----- Helfer -----
        private static readonly string[] OverlayExtensions = new[] { ".png", ".webp", ".jpg", ".jpeg" };

        private void BuildOverlayIndexIfNeeded(string targetFolder)
        {
            lock (_cacheLock)
            {
                if (Options == null) return;
                if (Options.qualified != null && Options.qualified.Count > 0) return;
                if (string.IsNullOrWhiteSpace(targetFolder) || !Directory.Exists(targetFolder)) return;

                var dict = new Dictionary<string, (string Path, string ModificationDate)>(StringComparer.Ordinal);
                foreach (var path in Directory.EnumerateFiles(targetFolder))
                {
                    var ext = Path.GetExtension(path);
                    if (!OverlayExtensions.Contains(ext, StringComparer.OrdinalIgnoreCase)) continue;

                    var name = Path.GetFileNameWithoutExtension(path);
                    var mod = File.GetLastWriteTime(path).ToString("yyyyMMddHHmmss", CultureInfo.InvariantCulture);
                    dict[name] = (path, mod);
                }
                Options.qualified = dict;
                _logger.Info($"Overlay index built: {Options.qualified.Count} entries in {targetFolder}");
            }
        }

        private bool TryGetOverlayFromCacheOrDisk(string targetFolder, string key, out (string Path, string ModificationDate) entry)
        {
            lock (_cacheLock)
            {
                // Sicherstellen, dass qualified initialisiert ist
                if (Options != null && Options.qualified == null)
                {
                    Options.qualified = new Dictionary<string, (string Path, string ModificationDate)>(StringComparer.Ordinal);
                }

                if (Options?.qualified != null && Options.qualified.TryGetValue(key, out var cachedEntry))
                {
                    // Check: Existiert die Datei noch und hat sich das Datum geändert?
                    var fileInfo = new FileInfo(cachedEntry.Path);
                    if (fileInfo.Exists)
                    {
                        var currentMod = fileInfo.LastWriteTime.ToString("yyyyMMddHHmmss", CultureInfo.InvariantCulture);
                        if (currentMod != cachedEntry.ModificationDate)
                        {
                            // Update Cache
                            entry = (cachedEntry.Path, currentMod);
                            Options.qualified[key] = entry;
                        }
                        else
                        {
                            entry = cachedEntry;
                        }
                        return true;
                    }
                    else
                    {
                        // Datei weg -> aus Cache entfernen
                        Options.qualified.Remove(key);
                    }
                }

                foreach (var ext in OverlayExtensions)
                {
                    var p = Path.Combine(targetFolder, key + ext);
                    var fileInfo = new FileInfo(p);
                    if (fileInfo.Exists)
                    {
                        var mod = fileInfo.LastWriteTime.ToString("yyyyMMddHHmmss", CultureInfo.InvariantCulture);
                        entry = (p, mod);
                        if (Options?.qualified != null)
                            Options.qualified[key] = entry;
                        return true;
                    }
                }

                entry = default;
                return false;
            }
        }

        private static async Task WaitForFileAccessAsync(string filePath)
        {
            for (int i = 0; i < 20; i++)
            {
                try
                {
                    using (var stream = File.Open(filePath, FileMode.Open, FileAccess.Read, FileShare.Read))
                    {
                        return;
                    }
                }
                catch (IOException)
                {
                    // File is locked
                }
                await Task.Delay(500).ConfigureAwait(false);
            }
        }

        // SkiaSharp 2.88.x: Resize mit SKFilterQuality.High
        private static SKBitmap ResizeOrCopy(SKBitmap src, int w, int h)
        {
            if (src.Width == w && src.Height == h)
                return src.Copy(); // tiefe Kopie

            var info = new SKImageInfo(w, h, src.ColorType, src.AlphaType, src.ColorSpace);
            return src.Resize(info, SKFilterQuality.High);
        }
    }
}