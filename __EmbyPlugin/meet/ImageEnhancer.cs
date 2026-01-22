﻿//using MediaBrowser.Controller.Entities;
//using MediaBrowser.Controller.Providers;
//using MediaBrowser.Model.Drawing;
//using MediaBrowser.Model.Entities;
//using MediaBrowser.Model.Logging;
//using System;
//using System.IO;
//using System.Threading.Tasks;
//using System.Collections.Generic;
//using EmbyPluginSimpleUi.Common;
//using MediaBrowser.Common;
//using System.Globalization;
//using System.Linq;
//using MediaBrowser.Controller.Base;
//using SkiaSharp;


////ToDo: Add JPG quality to options
//namespace EmbyPluginUiDemo.meet
//{
//    public sealed class ImageEnhancer : CommonBase, IImageEnhancer
//    {
//        private readonly ILogger _logger;
//        private readonly string _pluginVersion;

//        MetadataProviderPriority IImageEnhancer.Priority => MetadataProviderPriority.First;

//        public ImageEnhancer(ILogger logger, IApplicationHost appHost)
//            : base(new ServiceRoot(appHost))
//        {
//            _logger = logger;
//            _pluginVersion = typeof(ImageEnhancer).Assembly.GetName().Version?.ToString() ?? "0.0.0.0";
//            try
//            {
//                var asm = typeof(SKBitmap).Assembly;
//                _logger.Info($"ImageEnhancer v{_pluginVersion} | Skia: {asm.FullName}");
//            }
//            catch (Exception ex)
//            {
//                _logger.Error(ex + "Skia assembly probe failed");
//            }
//        }

//        // ----- Cache-Key steuert Re-Render -----
//        string IImageEnhancer.GetConfigurationCacheKey(BaseItem item, ImageType imageType)
//        {
//            var key = item.InternalId.ToString(CultureInfo.InvariantCulture);
//            var itemPart = $"{item.Id}_{imageType}_{item.DateModified.Ticks}";

//            if (Options?.qualified == null || !Options.qualified.TryGetValue(key, out var ov))
//                return $"{itemPart}_SkiaEnhanced_v{_pluginVersion}_NOOVERLAY";

//            return $"{itemPart}_SkiaEnhanced_v{_pluginVersion}_{ov.ModificationDate}";
//        }

//        EnhancedImageInfo IImageEnhancer.GetEnhancedImageInfo(BaseItem item, string inputFile, ImageType imageType, int imageIndex)
//            => new EnhancedImageInfo { RequiresTransparency = false };

//        ImageSize IImageEnhancer.GetEnhancedImageSize(BaseItem item, ImageType imageType, int imageIndex, ImageSize originalImageSize)
//        {
//            if (imageType != ImageType.Primary) return originalImageSize;
//            return new ImageSize { Width = 1000, Height = 1500 };
//        }

//        bool IImageEnhancer.Supports(BaseItem item, ImageType imageType)
//        {
//            if (imageType != ImageType.Primary) return false;

//            var targetFolder = Options?.TargetFolder;
//            if (string.IsNullOrWhiteSpace(targetFolder) || !Directory.Exists(targetFolder))
//                return false;

//            BuildOverlayIndexIfNeeded(targetFolder);

//            var key = item.InternalId.ToString(CultureInfo.InvariantCulture);
//            if (TryGetOverlayFromCacheOrDisk(targetFolder, key, out var entry))
//            {
//                //_logger.Info($"Support check for item: {item.Name}, Supported: True - File: {entry.Path}");
//                return true;
//            }
//            return false;
//        }

//        // ----- Render -----
//        async Task IImageEnhancer.EnhanceImageAsync(BaseItem item, string inputFile, string outputFile, ImageType imageType, int imageIndex)
//        {
//            if (imageType != ImageType.Primary) return;

//            var key = item.InternalId.ToString(CultureInfo.InvariantCulture);
//            var targetFolder = Options?.TargetFolder;

//            if (string.IsNullOrWhiteSpace(targetFolder) || Options?.qualified == null)
//                BuildOverlayIndexIfNeeded(targetFolder);

//            if (Options?.qualified == null || !Options.qualified.TryGetValue(key, out var ov))
//            {
//                _logger.Info($"Enhancer skipped: no overlay for {item.Name} ({key})");
//                return;
//            }

//            const int targetWidth = 1000;
//            const int targetHeight = 1500;

//            Directory.CreateDirectory(Path.GetDirectoryName(outputFile)!);

//            _logger.Info($"Enhancer start: {item.Name} | In: {inputFile} | Out: {outputFile} | Ov: {ov.Path}");

//            using var inputStream = File.Open(inputFile, FileMode.Open, FileAccess.Read, FileShare.Read);
//            using var overlayStream = File.Open(ov.Path, FileMode.Open, FileAccess.Read, FileShare.Read);
//            using var outputStream = File.Open(outputFile, FileMode.Create, FileAccess.Write, FileShare.None);

//            using var bitmap = SKBitmap.Decode(inputStream);
//            using var overlayBitmap = SKBitmap.Decode(overlayStream);

//            if (bitmap is null) { _logger.Error("Decode input failed"); return; }
//            if (overlayBitmap is null) { _logger.Error("Decode overlay failed"); return; }

//            using var resizedBitmap = ResizeOrCopy(bitmap, targetWidth, targetHeight);
//            if (resizedBitmap is null) { _logger.Error("Resize failed"); return; }

//            using var combinedBitmap = new SKBitmap(targetWidth, targetHeight);
//            using var canvas = new SKCanvas(combinedBitmap);
//            canvas.Clear(SKColors.Black);
//            canvas.DrawBitmap(resizedBitmap, 0, 0);

//            var overlayX = (targetWidth - overlayBitmap.Width) / 2;
//            var overlayY = (targetHeight - overlayBitmap.Height) / 2;
//            canvas.DrawBitmap(overlayBitmap, overlayX, overlayY);
//            canvas.Flush();

//            using var image = SKImage.FromBitmap(combinedBitmap);
//            using var data = image.Encode(SKEncodedImageFormat.Jpeg, 85);
//            data.SaveTo(outputStream);
//            await outputStream.FlushAsync().ConfigureAwait(false);

//            _logger.Info($"Enhancer done: {item.Name} → {outputFile}");
//        }

//        // ----- Helfer -----
//        private static readonly string[] OverlayExtensions = new[] { ".png", ".webp", ".jpg", ".jpeg" };

//        private void BuildOverlayIndexIfNeeded(string targetFolder)
//        {
//            if (Options == null) return;
//            if (Options.qualified != null && Options.qualified.Count > 0) return;
//            if (string.IsNullOrWhiteSpace(targetFolder) || !Directory.Exists(targetFolder)) return;

//            var dict = new Dictionary<string, (string Path, string ModificationDate)>(StringComparer.Ordinal);
//            foreach (var path in Directory.EnumerateFiles(targetFolder))
//            {
//                var ext = Path.GetExtension(path);
//                if (!OverlayExtensions.Contains(ext, StringComparer.OrdinalIgnoreCase)) continue;

//                var name = Path.GetFileNameWithoutExtension(path);
//                var mod = File.GetLastWriteTime(path).ToString("yyyyMMddHHmmss", CultureInfo.InvariantCulture);
//                dict[name] = (path, mod);
//            }
//            Options.qualified = dict;
//            _logger.Info($"Overlay index built: {Options.qualified.Count} entries in {targetFolder}");
//        }

//        private bool TryGetOverlayFromCacheOrDisk(string targetFolder, string key, out (string Path, string ModificationDate) entry)
//        {
//            if (Options?.qualified != null && Options.qualified.TryGetValue(key, out entry))
//                return true;

//            foreach (var ext in OverlayExtensions)
//            {
//                var p = Path.Combine(targetFolder, key + ext);
//                if (File.Exists(p))
//                {
//                    var mod = File.GetLastWriteTime(p).ToString("yyyyMMddHHmmss", CultureInfo.InvariantCulture);
//                    entry = (p, mod);
//                    if (Options?.qualified != null)
//                        Options.qualified[key] = entry;
//                    _logger.Warn($"Overlay auf Disk gefunden, aber nicht im Cache. Repariert: {key} -> {p}");
//                    return true;
//                }
//            }

//            entry = default;
//            return false;
//        }

//        // SkiaSharp 2.88.x: Resize mit SKFilterQuality.High
//        private static SKBitmap ResizeOrCopy(SKBitmap src, int w, int h)
//        {
//            if (src.Width == w && src.Height == h)
//                return src.Copy(); // tiefe Kopie

//            var info = new SKImageInfo(w, h, src.ColorType, src.AlphaType, src.ColorSpace);
//            return src.Resize(info, SKFilterQuality.High);
//        }
//    }
//}

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
            var itemPart = $"{item.Id}_{imageType}_{item.DateModified.Ticks}";
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

            if (string.IsNullOrWhiteSpace(targetFolder) || Options?.qualified == null)
                BuildOverlayIndexIfNeeded(targetFolder);

            if (Options?.qualified == null || !Options.qualified.TryGetValue(key, out var ov))
            {
                _logger.Info($"Enhancer skipped: no overlay for {item.Name} ({key})");
                return;
            }

            const int targetWidth = 1000;
            const int targetHeight = 1500;

            Directory.CreateDirectory(Path.GetDirectoryName(outputFile)!);

            _logger.Info($"Enhancer start: {item.Name} | In: {inputFile} | Out: {outputFile} | Ov: {ov.Path}");

            using var inputStream = File.Open(inputFile, FileMode.Open, FileAccess.Read, FileShare.Read);
            using var overlayStream = File.Open(ov.Path, FileMode.Open, FileAccess.Read, FileShare.Read);
            using var outputStream = File.Open(outputFile, FileMode.Create, FileAccess.Write, FileShare.None);

            using var bitmap = SKBitmap.Decode(inputStream);
            using var overlayBitmap = SKBitmap.Decode(overlayStream);

            if (bitmap is null) { _logger.Error("Decode input failed"); return; }
            if (overlayBitmap is null) { _logger.Error("Decode overlay failed"); return; }

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
            data.SaveTo(outputStream);
            await outputStream.FlushAsync().ConfigureAwait(false);

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