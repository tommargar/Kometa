namespace EmbyPluginSimpleUi.Common
{
    using System.Linq;
    using EmbyPluginSimpleUI;
    using EmbyKometaOverlays;
    using MediaBrowser.Common;
    using MediaBrowser.Controller.Base;
    using MediaBrowser.Model.Logging;
    using MediaBrowser.Model.System;

    public abstract class CommonBase : CommonBaseCore
    {

        private meetPlugin myPlugin;

        protected CommonBase(IServiceRoot serviceRoot, ILogger logger)
            : base(serviceRoot, logger)
        {
        }

        protected CommonBase(IServiceRoot serviceRoot, string logName = null)
            : base(serviceRoot, logName)
        {
        }

        protected MyPluginOptions Options => this.Plugin.Options;

        protected meetPlugin Plugin
        {
            get
            {
                if (this.myPlugin == null)
                {
                    this.myPlugin = this.GetService<IApplicationHost>().Plugins.OfType<meetPlugin>().FirstOrDefault();
                    if (this.myPlugin == null)
                    {
                        throw this.GetEx(@"The {0} plugin is not loaded", meetPlugin.PluginName);
                    }
                }

                return this.myPlugin;
            }
        }
    }
}
