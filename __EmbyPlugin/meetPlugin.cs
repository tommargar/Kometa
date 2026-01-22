// <copyright file="PluginUIDemoPlugin.cs" company="Emby LLC">
// Copyright © 2022 - Emby LLC. All rights reserved.
// </copyright>

namespace EmbyPluginUiDemo
{
    using System;
    using System.Collections.Generic;
    using System.IO;
    using EmbyPluginSimpleUI;
    using MediaBrowser.Common;
    using MediaBrowser.Common.Plugins;
    using MediaBrowser.Controller.Plugins;
    using MediaBrowser.Model.Drawing;
    using MediaBrowser.Model.Logging;

    /// <summary>
    /// The plugin.
    /// </summary>
    public class meetPlugin : BasePluginSimpleUI<MyPluginOptions>, IHasThumbImage
    {

        /// <summary>The Plugin ID.</summary>
        private readonly Guid id = new Guid("9D22C04B-744C-4F8A-A009-F4CFDCF73ECF");
        
        private readonly ILogger logger;

        /// <summary>Initializes a new instance of the <see cref="meetPlugin" /> class.</summary>
        /// <param name="applicationHost">The application host.</param>
        /// <param name="logManager">The log manager.</param>
        public meetPlugin(IApplicationHost applicationHost, ILogManager logManager) : base(applicationHost)
        {
            this.logger = logManager.GetLogger(this.Name);
            this.logger.Info("My plugin ({0}) is getting loaded", this.Name);

        }

        /// <summary>Gets the description.</summary>
        /// <value>The description.</value>
        public override string Description => "This plugin demonstrates the new Emby Plugin UI features";

        /// <summary>Gets the unique id.</summary>
        /// <value>The unique id.</value>
        public override Guid Id => this.id;

        /// <summary>Gets the name of the plugin</summary>
        /// <value>The name.</value>
        public const string PluginName = "meet Moovie manager";

        /// <summary>Gets the name of the plugin</summary>
        /// <value>The name.</value>
        public override sealed string Name => PluginName;

        /// <summary>Gets the plugin options.</summary>
        /// <value>The plugin options.</value>
        public MyPluginOptions Options => this.GetOptions();

        /// <summary>Gets the thumb image format.</summary>
        /// <value>The thumb image format.</value>
        public ImageFormat ThumbImageFormat => ImageFormat.Jpg;

        /// <summary>Gets the thumb image.</summary>
        /// <returns>An image stream.</returns>
        public Stream GetThumbImage()
        {
            var type = this.GetType();
            return type.Assembly.GetManifestResourceStream(type.Namespace + ".PluginUiThumb.jpg");
        }

        protected override void OnOptionsSaved(MyPluginOptions options)
        {
            options.qualified = null;
            this.logger.Info("My plugin ({0}) options have been updated.", this.Name);
            //if (this.qualified == null)

        }

    }
}