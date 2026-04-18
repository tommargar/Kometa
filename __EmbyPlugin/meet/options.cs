namespace EmbyPluginSimpleUI
{
    using System;
    using System.Collections.Generic;
    using System.ComponentModel;
    using System.IO;
    using Emby.Web.GenericEdit;
    using Emby.Web.GenericEdit.Validation;

    using MediaBrowser.Model.Attributes;
    using MediaBrowser.Model.Logging;

    public class MyPluginOptions : EditableOptionsBase
    {
        public override string EditorTitle => "Emby Kometa Overlays - Settings";

        public override string EditorDescription => "This is the configuration page for Emby Kometa Overlays.\n"
                                                    + "Please configure the settings below to customize the plugin behavior.";

        [DisplayName("Output Folder")]
        [Description("Please point to the Kometa output folder with the opaque pngs.")]
        [EditFolderPicker]
        public string TargetFolder { get; set; }

        public Dictionary<string, (string Path, string ModificationDate)> qualified { get; set; }

        //[Description("The log level determines how messages will be logged")]
        //public LogSeverity LogLevel { get; set; }

        //[Description("This value is required and needs to have a minimum length of 10")]
        //[MediaBrowser.Model.Attributes.Required]
        //public string MessageFormat { get; set; }

        protected override void Validate(ValidationContext context)
        {

            if (File.Exists(this.TargetFolder))
            {
                //logger.Info($"TargetFolder exists {targetFolder}");
            }


            //foreach (string file in files)
            //{
            //    logger.Info(file);
            //}
            //this.Options.qualified = files.ToList();
            //}

            //if (!(this.MessageFormat?.Length >= 10))
            //{
            //    context.AddValidationError(nameof(this.MessageFormat), "Minimum length is 10 characters");
            //}
        }


    }
}
