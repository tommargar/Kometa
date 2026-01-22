using SkiaSharp;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace EmbyPluginUiDemo.meet
{
    public class RibbonProcessor
    {
        private const int PosterWidth = 1000;
        private const int PosterHeight = 1500;
        private const int RibbonSize = 351; // Ribbons are 351x351 pixels

        public void ApplyRibbonsToPoster(string posterPath, string outputPath, List<string> ribbonPaths)
        {
            using (var posterBitmap = SKBitmap.Decode(posterPath))
            using (var canvas = new SKCanvas(posterBitmap))
            {
                int xPosition = PosterWidth - RibbonSize; // Right edge
                int yPosition = PosterHeight - RibbonSize; // Bottom edge

                foreach (var ribbonPath in ribbonPaths.OrderBy(r => r)) // Apply sorted ribbons
                {
                    using (var ribbonBitmap = SKBitmap.Decode(ribbonPath))
                    {
                        var destRect = new SKRect(xPosition, yPosition, xPosition + RibbonSize, yPosition + RibbonSize);
                        canvas.DrawBitmap(ribbonBitmap, destRect);
                    }
                }

                // Save the poster with ribbons
                using (var image = SKImage.FromBitmap(posterBitmap))
                using (var data = image.Encode(SKEncodedImageFormat.Png, 100))
                {
                    using (var output = File.OpenWrite(outputPath))
                    {
                        data.SaveTo(output);
                    }
                }
            }
        }
    }
}