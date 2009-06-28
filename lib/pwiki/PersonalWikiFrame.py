## import hotshot
## _prof = hotshot.Profile("hotshot.prf")

import os, sys, gc, traceback, sets, string, re
from os.path import *
from time import localtime, time, sleep

import cPickle  # to create dependency?

import wx, wx.html

# import urllib_red as urllib
# import urllib

from wxHelper import GUI_ID, getAccelPairFromKeyDown, \
        getAccelPairFromString, LayerSizer, appendToMenuByMenuDesc, \
        setHotKeyByString, DummyWindow, IdRecycler, clearMenu, \
        copyTextToClipboard

import TextTree

from MiscEvent import MiscEventSourceMixin, ProxyMiscEvent  # , DebugSimple

from WikiExceptions import *
from Consts import HOMEPAGE

import Configuration
from WindowLayout import WindowSashLayouter, setWindowPos, setWindowSize

from wikidata import DbBackendUtils, WikiDataManager

import OsAbstract

import DocPages, WikiFormatting


from CmdLineAction import CmdLineAction
from WikiTxtCtrl import WikiTxtCtrl, FOLD_MENU
from WikiTreeCtrl import WikiTreeCtrl
from WikiHtmlView import createWikiHtmlView
from LogWindow import LogWindow
from DocStructureCtrl import DocStructureCtrl
from timeView.TimeViewCtrl import TimeViewCtrl
from MainAreaPanel import MainAreaPanel
from UserActionCoord import UserActionCoord
from DocPagePresenter import DocPagePresenter

from Ipc import EVT_REMOTE_COMMAND

import PropertyHandling, SpellChecker

# from PageHistory import PageHistory
 #from SearchAndReplace import SearchReplaceOperation
from Printing import Printer, PrintMainDialog

from AdditionalDialogs import *
from OptionsDialog import OptionsDialog
from SearchAndReplaceDialogs import *



import Exporters
from StringOps import uniToGui, guiToUni, mbcsDec, mbcsEnc, strToBool, \
        BOM_UTF8, fileContentToUnicode, splitIndent, \
        unescapeWithRe, escapeForIni, unescapeForIni, \
        wikiUrlToPathWordAndAnchor, urlFromPathname, flexibleUrlUnquote, \
        strftimeUB, pathEnc, loadEntireTxtFile, writeEntireTxtFile, \
        pathWordAndAnchorToWikiUrl, relativeFilePath, pathnameFromUrl

import DocPages
import WikiFormatting


# import PageAst   # For experiments only

from PluginManager import *

# TODO More abstract/platform independent
try:
    import WindowsHacks
except:
    if Configuration.isWindows():
        traceback.print_exc()
    WindowsHacks = None



class wxGuiProgressHandler:
    """
    Implementation of a GuiProgressListener to
    show a wxProgressDialog
    """
    def __init__(self, title, msg, addsteps, parent, flags=wx.PD_APP_MODAL):
        self.title = title
        self.msg = msg
        self.addsteps = addsteps
        self.parent = parent
        self.flags = flags

    def open(self, sum):
        """
        Start progress handler, set the number of steps, the operation will
        take in sum. Will be called once before update()
        is called several times
        """
        self.progDlg = wx.ProgressDialog(self.title, self.msg,
                sum + self.addsteps, self.parent, self.flags)
        
    def update(self, step, msg):
        """
        Called after a step is finished to trigger update
        of GUI.
        step -- Number of done steps
        msg -- Human readable descripion what is currently done
        returns: True to continue, False to stop operation
        """
        self.progDlg.Update(step, uniToGui(msg))
        return True

    def close(self):
        """
        Called after finishing operation or after abort to 
        do clean-up if necessary
        """
        self.progDlg.Destroy()
        self.progDlg = None


class KeyBindingsCache:
    def __init__(self, kbModule):
        self.kbModule = kbModule
        self.accelPairCache = {}
        
    def __getattr__(self, attr):
        return getattr(self.kbModule, attr, u"")
    
    def get(self, attr, default=None):
        return getattr(self.kbModule, attr, None)

    def getAccelPair(self, attr):
        try:
            return self.accelPairCache[attr]
        except KeyError:
            ap = getAccelPairFromString("\t" + getattr(self, attr))
            self.accelPairCache[attr] = ap
            return ap

    def matchesAccelPair(self, attr, accP):
        return self.getAccelPair(attr) == accP


class LossyWikiCloseDeniedException(Exception):
    """
    Special exception thrown by PersonalWikiFrame.closeWiki() if user denied
    to close the wiki because it might lead to data loss
    """
    pass



def _buildChainedUpdateEventFct(chain):
    def evtFct(evt):
        evt.Enable(True)
        for fct in chain:
            fct(evt)
        
    return evtFct


# def _buildUpdateEventFctByEnableExpress(expr):
#     def evtFct(evt):
#         
#         
#         evt.Enable(True)
#         for fct in chain:
#             fct(evt)
#         
#     return evtFct

    

class PersonalWikiFrame(wx.Frame, MiscEventSourceMixin):
    HOTKEY_ID_HIDESHOW_BYAPP = 1
    HOTKEY_ID_HIDESHOW_BYWIKI = 2

    def __init__(self, parent, id, title, wikiAppDir, globalConfigDir,
            globalConfigSubDir, cmdLineAction):
        wx.Frame.__init__(self, parent, -1, title, size = (700, 550),
                         style=wx.DEFAULT_FRAME_STYLE|wx.NO_FULL_REPAINT_ON_RESIZE)
        MiscEventSourceMixin.__init__(self)

        if cmdLineAction.cmdLineError:
            cmdLineAction.showCmdLineUsage(self,
                    _(u"Bad formatted command line.") + u"\n\n")
            self.Close()
            self.Destroy()
            return

        self.sleepMode = False  # Is program in low resource sleep mode?

#         if not globalConfigDir or not exists(globalConfigDir):
#             self.displayErrorMessage(
#                     u"Error initializing environment, couldn't locate "+
#                     u"global config directory", u"Shutting Down")
#             self.Close()


        # initialize some variables
        self.globalConfigDir = globalConfigDir
        self.wikiAppDir = wikiAppDir

        self.globalConfigSubDir = globalConfigSubDir

        # Create the "[TextBlocks].wiki" file in the global config subdirectory
        # if the file doesn't exist yet.
        tbLoc = join(self.globalConfigSubDir, "[TextBlocks].wiki")
        if not exists(pathEnc(tbLoc)):
            writeEntireTxtFile(tbLoc, (BOM_UTF8, 
"""importance: high;a=[importance: high]\\n
importance: low;a=[importance: low]\\n
tree_position: 0;a=[tree_position: 0]\\n
wrap: 80;a=[wrap: 80]\\n
camelCaseWordsEnabled: false;a=[camelCaseWordsEnabled: false]\\n
"""))
#         self.globalConfigLoc = join(globalConfigDir, "WikidPad.config")
        self.configuration = wx.GetApp().createCombinedConfiguration()
        
        # Listen to application events
        wx.GetApp().getMiscEvent().addListener(self)

        self.wikiPadHelp = join(self.wikiAppDir, 'WikidPadHelp',
                'WikidPadHelp.wiki')
        self.windowLayouter = None  # will be set by initializeGui()

        # defaults
        self.wikiData = None
        self.wikiDataManager = None
        self.lastCursorPositionInPage = {}
        self.wikiHistory = []
        self.findDlg = None  # Stores find&replace or wiki search dialog, if present
        self.spellChkDlg = None  # Stores spell check dialog, if present
        self.mainAreaPanel = None
#         self._mainAreaPanelCreated = False
        self.mainmenu = None

        self.recentWikisMenu = None
        self.recentWikisActivation = IdRecycler()

        self.textBlocksMenu = None
        self.textBlocksActivation = IdRecycler() # See self.fillTextBlocksMenu()

        self.favoriteWikisMenu = None
        self.favoriteWikisActivation = IdRecycler() 

        self.pluginsMenu = None
        self.fastSearchField = None   # Text field in toolbar
        
        self.cmdIdToIconName = None # Maps command id (=menu id) to icon name
                                    # needed for "Editor"->"Add icon attribute"
        self.cmdIdToColorName = None # Same for color names

        self.eventRoundtrip = 0

#         self.currentDocPagePresenterProxyEvent = ProxyMiscEvent(self)
        self.currentWikiDocumentProxyEvent = ProxyMiscEvent(self)
        self.currentWikiDocumentProxyEvent.addListener(self)

        # setup plugin manager and hooks API
        self.pluginManager = PluginManager()
        self.hooks = self.pluginManager.registerPluginAPI(("hooks",1),
            ["startup", "newWiki", "createdWiki", "openWiki", "openedWiki", 
             "openWikiWord", "newWikiWord", "openedWikiWord", "savingWikiWord",
             "savedWikiWord", "renamedWikiWord", "deletedWikiWord", "exit"] )
        # interfaces for menu and toolbar plugins
        self.menuFunctions = self.pluginManager.registerPluginAPI(("MenuFunctions",1), 
                                ("describeMenuItems",))
        self.toolbarFunctions = self.pluginManager.registerPluginAPI(("ToolbarFunctions",1), 
                                ("describeToolbarItems",))

        # load extensions
        self.loadExtensions()

        # initialize the wiki syntax
        WikiFormatting.initialize(self.wikiSyntax)

#         # Initialize new component
#         self.formatting = WikiFormatting.WikiFormatting(self, self.wikiSyntax)

        self.propertyChecker = PropertyHandling.PropertyChecker(self)

        self.configuration.setGlobalConfig(wx.GetApp().getGlobalConfig())

        # trigger hook
        self.hooks.startup(self)

#         # Connect page history
#         self.pageHistory = PageHistory(self)

        # Initialize printing
        self.printer = Printer(self)

        # wiki history
        history = self.configuration.get("main", "wiki_history")
        if history:
            self.wikiHistory = history.split(u";")

        # clipboard catcher  
        if WindowsHacks is None:
            self.clipboardInterceptor = None
            self.browserMoveInterceptor = None
            self._interceptCollection = None
        else:
            self.clipboardInterceptor = WindowsHacks.ClipboardCatchIceptor(self)
            self.browserMoveInterceptor = WindowsHacks.BrowserMoveIceptor(self)

            self._interceptCollection = WindowsHacks.WinProcInterceptCollection(
                    (self.clipboardInterceptor,
                    self.browserMoveInterceptor))
            self._interceptCollection.start(self.GetHandle())

#             self.clipboardInterceptor.intercept(self.GetHandle())

        # resize the window to the last position/size
        setWindowSize(self, (self.configuration.getint("main", "size_x", 200),
                self.configuration.getint("main", "size_y", 200)))
        setWindowPos(self, (self.configuration.getint("main", "pos_x", 10),
                self.configuration.getint("main", "pos_y", 10)))

        # Set the auto save timing
        self.autoSaveDelayAfterKeyPressed = self.configuration.getint(
                "main", "auto_save_delay_key_pressed")
        self.autoSaveDelayAfterDirty = self.configuration.getint(
                "main", "auto_save_delay_dirty")

        # Should reduce resources usage (less icons)
        # Do not set self.lowResources after initialization here!
        self.lowResources = wx.GetApp().getLowResources()
#         self.lowResources = self.configuration.getboolean("main", "lowresources")

#         # get the wrap mode setting
#         self.wrapMode = self.configuration.getboolean("main", "wrap_mode")

        # get the position of the splitter
        self.lastSplitterPos = self.configuration.getint("main", "splitter_pos")

        # get the default font for the editor
#         self.defaultEditorFont = self.configuration.get("main", "font",
#                 self.presentationExt.faces["mono"])
                
        self.layoutMainTreePosition = self.configuration.getint("main",
                "mainTree_position", 0)
        self.layoutViewsTreePosition = self.configuration.getint("main",
                "viewsTree_position", 0)
        self.layoutDocStructurePosition = self.configuration.getint("main",
                "docStructure_position", 0)
        self.layoutTimeViewPosition = self.configuration.getint("main",
                "timeView_position", 0)

        # this will keep track of the last font used in the editor
        self.lastEditorFont = None

        # should WikiWords be enabled or not for the current wiki
        self.wikiWordsEnabled = True

        # if a wiki to open wasn't passed in use the last_wiki from the global config
        wikiToOpen = cmdLineAction.wikiToOpen
        wikiWordsToOpen = cmdLineAction.wikiWordsToOpen
        anchorToOpen = cmdLineAction.anchorToOpen

        if not wikiToOpen:
            wikiToOpen = self.configuration.get("main", "last_wiki")

        # initialize the GUI
        self.initializeGui()

        # Minimize on tray?
        ## self.showOnTray = self.globalConfig.getboolean("main", "showontray")

        self.tbIcon = None
        self.setShowOnTray()

        # windowmode:  0=normal, 1=maximized, 2=iconized, 3=maximized iconized(doesn't work)
        windowmode = self.configuration.getint("main", "windowmode")

        if windowmode & 1:
            self.Maximize(True)
        if windowmode & 2:
            self.Iconize(True)
            
        # Set app-bound hot key
        self.hotKeyDummyWindow = None
        self._refreshHotKeys()

        self.windowLayouter.layout()
        
        # GUI construction finished, but window is hidden yet

        # if a wiki to open is set, open it
        if wikiToOpen:
            if exists(pathEnc(wikiToOpen)):
                self.openWiki(wikiToOpen, wikiWordsToOpen,
                anchorToOpen=anchorToOpen)
            else:
                self.statusBar.SetStatusText(
                        uniToGui(_(u"Last wiki doesn't exist: %s") % wikiToOpen), 0)

        cmdLineAction.actionBeforeShow(self)

        if cmdLineAction.exitFinally:
            self.exitWiki()
            return

        self.userActionCoord = UserActionCoord(self)
        self.userActionCoord.applyConfiguration()

        self.Show(True)

        if self.lowResources and self.IsIconized():
            self.resourceSleep()

        EVT_REMOTE_COMMAND(self, self.OnRemoteCommand)

#         wx.FileSystem.AddHandler(wx.ZipFSHandler())


    def loadExtensions(self):
        self.wikidPadHooks = self.getExtension('WikidPadHooks', u'WikidPadHooks.py')
        self.keyBindings = KeyBindingsCache(
                self.getExtension('KeyBindings', u'KeyBindings.py'))
        self.evalLib = self.getExtension('EvalLibrary', u'EvalLibrary.py')
        self.wikiSyntax = self.getExtension('SyntaxLibrary', u'WikiSyntax.py')
        self.presentationExt = self.getExtension('Presentation', u'Presentation.py')
        dirs = ( join(self.globalConfigSubDir, u'user_extensions'),
                join(self.wikiAppDir, u'user_extensions'),
                join(self.wikiAppDir, u'extensions') )
        self.pluginManager.loadPlugins( dirs, [ u'KeyBindings.py',
                u'EvalLibrary.py', u'WikiSyntax.py' ] )


    def getExtension(self, extensionName, fileName):
        extensionFileName = join(self.globalConfigSubDir, u'user_extensions',
                fileName)
        if exists(pathEnc(extensionFileName)):
            userUserExtension = loadEntireTxtFile(extensionFileName)
        else:
            userUserExtension = None

        extensionFileName = join(self.wikiAppDir, 'user_extensions', fileName)
        if exists(pathEnc(extensionFileName)):
            userExtension = loadEntireTxtFile(extensionFileName)
        else:
            userExtension = None

        extensionFileName = join(self.wikiAppDir, 'extensions', fileName)
        systemExtension = loadEntireTxtFile(extensionFileName)

        return importCode(systemExtension, userExtension, userUserExtension,
                extensionName)


    def getCurrentWikiWord(self):
        docPage = self.getCurrentDocPage()
        if docPage is None or not isinstance(docPage,
                (DocPages.WikiPage, DocPages.AliasWikiPage)):
            return None
        return docPage.getWikiWord()

    def getCurrentDocPage(self):
        if self.getCurrentDocPagePresenter() is None:
            return None
        return self.getCurrentDocPagePresenter().getDocPage()

    def getActiveEditor(self):
        return self.getCurrentDocPagePresenter().getSubControl("textedit")

    def getMainAreaPanel(self):
        return self.mainAreaPanel

    def getCurrentDocPagePresenter(self):
        """
        Convenience function
        """
        if self.mainAreaPanel is None:
            return None

        return self.mainAreaPanel.getCurrentDocPagePresenter()

    def getCurrentDocPagePresenterProxyEvent(self):
        """
        This ProxyMiscEvent resends any messsages from the currently
        active DocPagePresenter
        """
#         return self.currentDocPagePresenterProxyEvent
        return self.mainAreaPanel.getCurrentDocPagePresenterProxyEvent()

    def getCurrentWikiDocumentProxyEvent(self):
        """
        This ProxyMiscEvent resends any messsages from the currently
        active WikiDocument
        """
        return self.currentWikiDocumentProxyEvent

    def getWikiData(self):
        if self.wikiDataManager is None:
            return None

        return self.wikiDataManager.getWikiData()

    def getWikiDataManager(self):
        """
        Deprecated, use getWikiDocument() instead
        """
        return self.wikiDataManager

    def getWikiDocument(self):
        return self.wikiDataManager

    def isWikiLoaded(self):
        return self.getWikiDocument() is not None

    def getWikiConfigPath(self):
        if self.wikiDataManager is None:
            return None

        return self.wikiDataManager.getWikiConfigPath()

    def getConfig(self):
        return self.configuration

    def getFormatting(self):
        if self.wikiDataManager is None:
            return None

        return self.wikiDataManager.getFormatting()

    def getCollator(self):
        return wx.GetApp().getCollator()

    def getLogWindow(self):
        return self.logWindow

    def getKeyBindings(self):
        return self.keyBindings
        
    def getClipboardInterceptor(self):
        return self.clipboardInterceptor

    def getUserActionCoord(self):
        return self.userActionCoord

    def lookupIcon(self, iconname):
        """
        Returns the bitmap object for the given iconname.
        If the bitmap wasn't cached already, it is loaded and created.
        If icon is unknown, None is returned.
        """
        return wx.GetApp().getIconCache().lookupIcon(iconname)

    def lookupSystemIcon(self, iconname):
        """
        Returns the bitmap object for the given iconname.
        If the bitmap wasn't cached already, it is loaded and created.
        If icon is unknown, an error message is shown and an empty
        black bitmap is returned.
        """
        icon = wx.GetApp().getIconCache().lookupIcon(iconname)
        if icon is None:
            icon = wx.EmptyBitmap(16, 16)
            self.displayErrorMessage(_(u'Error, icon "%s" missing.' % iconname))

        return icon


    def lookupIconIndex(self, iconname):
        """
        Returns the id number into self.iconImageList of the requested icon.
        If icon is unknown, -1 is returned.
        """
        return wx.GetApp().getIconCache().lookupIconIndex(iconname)


    def resolveIconDescriptor(self, desc, default=None):
        """
        Used for plugins of type "MenuFunctions" or "ToolbarFunctions".
        Tries to find and return an appropriate wx.Bitmap object.
        
        An icon descriptor can be one of the following:
            - None
            - a wx.Bitmap object
            - the filename of a bitmap
            - a tuple of filenames, first existing file is used
        
        If no bitmap can be found, default is returned instead.
        """
        return wx.GetApp().getIconCache().resolveIconDescriptor(desc, default)


    def _OnRoundtripEvent(self, evt):
        """
        Special event handler for events which must be handled by the
        window which has currently the focus (e.g. "copy to clipboard" which
        must be done by either editor or HTML preview).
        
        These events are sent further to the currently focused window.
        If they are not consumed they go up to the parent window until
        they are here again (make a "roundtrip").
        This function also avoids an infinite loop of such events.
        """
        # Check for infinite loop
        if self.eventRoundtrip > 0:
            return

        self.eventRoundtrip += 1
        try:
            focus = wx.Window.FindFocus()
            if focus is not None:
                focus.ProcessEvent(evt)
        finally:
            self.eventRoundtrip -= 1


    def _OnEventToCurrentDocPPresenter(self, evt):
        """
        wx events which should be sent to current doc page presenter
        """
        # Check for infinite loop
        if self.eventRoundtrip > 0:
            return

        dpp = self.getCurrentDocPagePresenter()
        if dpp is None:
            return

        self.eventRoundtrip += 1
        try:
            dpp.ProcessEvent(evt)
        finally:
            self.eventRoundtrip -= 1


    def addMenuItem(self, menu, label, text, evtfct=None, icondesc=None,
            menuID=None, updatefct=None, kind=wx.ITEM_NORMAL):
        if menuID is None:
            menuID = wx.NewId()
            
        if kind is None:
            kind = wx.ITEM_NORMAL

        menuitem = wx.MenuItem(menu, menuID, label, text, kind)
        # if icondesc:  # (not self.lowResources) and
        bitmap = self.resolveIconDescriptor(icondesc)
        if bitmap:
            menuitem.SetBitmap(bitmap)

        menu.AppendItem(menuitem)
        if evtfct is not None:
            wx.EVT_MENU(self, menuID, evtfct)

        if updatefct is not None:
            if isinstance(updatefct, tuple):
                updatefct = _buildChainedUpdateEventFct(updatefct)
            wx.EVT_UPDATE_UI(self, menuID, updatefct)

        return menuitem


    def buildWikiMenu(self):
        """
        Builds the first, the "Wiki" menu and returns it
        """
        wikiData = self.getWikiData()
        wikiMenu = wx.Menu()

        self.addMenuItem(wikiMenu, _(u'&New') + u'\t' + self.keyBindings.NewWiki,
                _(u'New Wiki'), self.OnWikiNew)

        self.addMenuItem(wikiMenu, _(u'&Open') + u'\t' + self.keyBindings.OpenWiki,
                _(u'Open Wiki'), self.OnWikiOpen)

## TODO
        self.addMenuItem(wikiMenu, _(u'&Open in New Window') + u'\t' +
                self.keyBindings.OpenWikiNewWindow,
                _(u'Open Wiki in a new window'), self.OnWikiOpenNewWindow)

        self.addMenuItem(wikiMenu, _(u'Open as &Type'),
                _(u'Open Wiki with a specified wiki database type'),
                self.OnWikiOpenAsType)

        self.recentWikisMenu = wx.Menu()
        wikiMenu.AppendMenu(wx.NewId(), _(u'&Recent'), self.recentWikisMenu)

#         for i in xrange(15):
#             menuID = getattr(GUI_ID, "CMD_OPEN_RECENT_WIKI%i" % i)
#             wx.EVT_MENU(self, menuID, self.OnSelectRecentWiki)

        self.rereadRecentWikis()


        wikiMenu.AppendSeparator()

        if wikiData is not None:
#             wikiMenu.AppendSeparator()

            self.addMenuItem(wikiMenu, _(u'&Search Wiki') + u'\t' +
                    self.keyBindings.SearchWiki, _(u'Search Wiki'),
                    lambda evt: self.showSearchDialog(), "tb_lens")


        self.addMenuItem(wikiMenu, _(u'O&ptions...'),
                _(u'Set Options'), lambda evt: self.showOptionsDialog(),
                menuID = wx.ID_PREFERENCES)

        wikiMenu.AppendSeparator()

        if wikiData is not None:
            exportWikisMenu = wx.Menu()
            wikiMenu.AppendMenu(wx.NewId(), _(u'Export'), exportWikisMenu)
    
            self.addMenuItem(exportWikisMenu,
                    _(u'Export Wiki as Single HTML Page'),
                    _(u'Export Wiki as Single HTML Page'), self.OnExportWiki,
                    menuID=GUI_ID.MENU_EXPORT_WHOLE_AS_PAGE)
    
            self.addMenuItem(exportWikisMenu,
                    _(u'Export Wiki as Set of HTML Pages'),
                    _(u'Export Wiki as Set of HTML Pages'), self.OnExportWiki,
                    menuID=GUI_ID.MENU_EXPORT_WHOLE_AS_PAGES)
    
            self.addMenuItem(exportWikisMenu,
                    _(u'Export Current Wiki Word as HTML Page'),
                    _(u'Export Current Wiki Word as HTML Page'), self.OnExportWiki,
                    menuID=GUI_ID.MENU_EXPORT_WORD_AS_PAGE)
    
            self.addMenuItem(exportWikisMenu,
                    _(u'Export Sub-Tree as Single HTML Page'),
                    _(u'Export Sub-Tree as Single HTML Page'), self.OnExportWiki,
                    menuID=GUI_ID.MENU_EXPORT_SUB_AS_PAGE)
    
            self.addMenuItem(exportWikisMenu,
                    _(u'Export Sub-Tree as Set of HTML Pages'),
                    _(u'Export Sub-Tree as Set of HTML Pages'), self.OnExportWiki,
                    menuID=GUI_ID.MENU_EXPORT_SUB_AS_PAGES)
    
#             self.addMenuItem(exportWikisMenu,
#                     _(u'Export Wiki as XML'),
#                     _(u'Export Wiki as XML in UTF-8'), self.OnExportWiki,
#                     menuID=GUI_ID.MENU_EXPORT_WHOLE_AS_XML)
    
            self.addMenuItem(exportWikisMenu,
                    _(u'Export Wiki to .wiki files'),
                    _(u'Export Wiki to .wiki files in UTF-8'), self.OnExportWiki,
                    menuID=GUI_ID.MENU_EXPORT_WHOLE_AS_RAW)
    
            self.addMenuItem(exportWikisMenu, _(u'Other Export...'),
                    _(u'Open export dialog'), self.OnCmdExportDialog)

        if wikiData is not None:
            self.addMenuItem(wikiMenu, _(u'Import...'),
                    _(u'Import dialog'), self.OnCmdImportDialog,
                    updatefct=self.OnUpdateDisReadOnlyWiki)


        if wikiData is not None:
            self.addMenuItem(wikiMenu, _(u'Print...') + u'\t' + self.keyBindings.Print,
                    _(u'Show the print dialog'),
                    lambda evt: self.printer.showPrintMainDialog())

        if wikiData is not None and wikiData.checkCapability("rebuild") == 1:
            self.addMenuItem(wikiMenu, _(u'&Rebuild Wiki'),
                    _(u'Rebuild this wiki'), lambda evt: self.rebuildWiki(),
                    menuID=GUI_ID.MENU_REBUILD_WIKI,
                    updatefct=self.OnUpdateDisReadOnlyWiki)

#             wikiMenu.Append(GUI_ID.MENU_REBUILD_WIKI, '&Rebuild Wiki',
#                     'Rebuild this wiki')
#             wx.EVT_MENU(self, GUI_ID.MENU_REBUILD_WIKI,
#                     lambda evt: self.rebuildWiki())

        if wikiData is not None:
            self.addMenuItem(wikiMenu, _(u'Reconnect'),
                    _(u'Reconnect to database after connection failure'),
                    self.OnCmdReconnectDatabase)

        if wikiData is not None and wikiData.checkCapability("compactify") == 1:
            self.addMenuItem(wikiMenu, _(u'&Vacuum Wiki'),
                    _(u'Free unused space in database'),
                    lambda evt: self.vacuumWiki(),
                    menuID=GUI_ID.MENU_VACUUM_WIKI,
                    updatefct=self.OnUpdateDisReadOnlyWiki)

#             wikiMenu.Append(GUI_ID.MENU_VACUUM_WIKI, '&Vacuum Wiki',
#                     'Free unused space in database')
#             wx.EVT_MENU(self, GUI_ID.MENU_VACUUM_WIKI,
#                     lambda evt: self.vacuumWiki())

        if wikiData is not None and \
                wikiData.checkCapability("plain text import") == 1:
            self.addMenuItem(wikiMenu, _(u'&Copy .wiki files to database'),
                    _(u'Copy .wiki files to database'),
                    self.OnImportFromPagefiles,
                    updatefct=self.OnUpdateDisReadOnlyWiki)

#             menuID=wx.NewId()
#             wikiMenu.Append(menuID, '&Copy .wiki files to database', 'Copy .wiki files to database')
#             wx.EVT_MENU(self, menuID, self.OnImportFromPagefiles)

        if wikiData is not None:
            wikiMenu.AppendSeparator()            
            self.addMenuItem(wikiMenu, _(u'Wiki &Info...'),
                    _(u'Show general information about current wiki'),
                    self.OnShowWikiInfoDialog)

        if wikiData is not None and wikiData.checkCapability("versioning") == 1:
            wikiMenu.AppendSeparator()
    
#             menuID=wx.NewId()
#             wikiMenu.Append(menuID, '&Store version', 'Store new version')
#             wx.EVT_MENU(self, menuID, lambda evt: self.showStoreVersionDialog())
    
            menuID=wx.NewId()
            wikiMenu.Append(menuID, _(u'&Retrieve version'),
                    _(u'Retrieve previous version'))
            wx.EVT_MENU(self, menuID, lambda evt: self.showSavedVersionsDialog())
    
            menuID=wx.NewId()
            wikiMenu.Append(menuID, _(u'Delete &All Versions'),
                    _(u'Delete all stored versions'))
            wx.EVT_MENU(self, menuID, lambda evt: self.showDeleteAllVersionsDialog())

        wikiMenu.AppendSeparator()  # TODO May have two separators without anything between

#         self.addMenuItem(wikiMenu, '&Test', 'Test', lambda evt: self.testIt())

        menuID=wx.NewId()
        wikiMenu.Append(menuID, _(u'E&xit'), _(u'Exit'))
        wx.EVT_MENU(self, menuID, lambda evt: self.exitWiki())
        wx.App.SetMacExitMenuItemId(menuID)

        return wikiMenu


    def fillPluginsMenu(self, pluginMenu):
        """
        Builds or rebuilds the plugin menu. This function does no id reuse
        so it shouldn't be called too often (mainly on start and when
        rebuilding menu during development of plugins)

        pluginMenu -- An empty wx.Menu to add items to
        """
#         pluginMenu = None
        # get info for any plugin menu items and create them as necessary
        menuItems = reduce(lambda a, b: a+list(b),
                self.menuFunctions.describeMenuItems(self), [])
        
        subStructure = {}

        if len(menuItems) > 0:
            def addPluginMenuItem(function, label, statustext, icondesc=None,
                    menuID=None, updateFunction=None, kind=None):
                
                labelComponents = label.split(u"|")
                
                sub = subStructure
                menu = pluginMenu

                for comp in labelComponents[:-1]:
                    newMenu, newSub = sub.get(comp, (None, None))
                    if newMenu is None:
                        newMenu = wx.Menu()
                        menu.AppendMenu(-1, comp, newMenu)
                        newSub = {}
                        sub[comp] = newMenu, newSub
                    
                    menu = newMenu
                    sub = newSub

                if updateFunction is not None:
                    updateFct = lambda evt: updateFunction(self, evt)
                else:
                    updateFct = None

                self.addMenuItem(menu, labelComponents[-1], statustext,
                        lambda evt: function(self, evt), icondesc, menuID,
                        updateFct, kind)

            for item in menuItems:
                addPluginMenuItem(*item)


    def fillRecentWikisMenu(self, menu):
        """
        Refreshes the list of recent wiki menus from self.wikiHistory
        """
        idRecycler = self.recentWikisActivation
        idRecycler.clearAssoc()

        # Add new items
        for wiki in self.wikiHistory:
            menuID, reused = idRecycler.assocGetIdAndReused(wiki)

            if not reused:
                # For a new id, an event must be set
                wx.EVT_MENU(self, menuID, self.OnRecentWikiUsed)

            menu.Append(menuID, uniToGui(wiki))


    def OnRecentWikiUsed(self, evt):
        entry = self.recentWikisActivation.get(evt.GetId())

        if entry is None:
            return

        self.openWiki(entry)


    def rereadRecentWikis(self):
        """
        Starts rereading and rebuilding of the recent wikis submenu
        """
        if self.recentWikisMenu is None:
            return
        
        history = self.configuration.get("main", "wiki_history")
        if not history:
            return
        
        self.wikiHistory = history.split(u";")
        
        maxLen = self.configuration.getint(
                "main", "recentWikisList_length", 5)
        if len(self.wikiHistory) > maxLen:
            self.wikiHistory = self.wikiHistory[:maxLen]

        clearMenu(self.recentWikisMenu)
        self.fillRecentWikisMenu(self.recentWikisMenu)


    def informRecentWikisChanged(self):
        self.configuration.set("main", "wiki_history",
                ";".join(self.wikiHistory))
        wx.GetApp().fireMiscEventKeys(
                ("reread recent wikis needed",))

    def fillTextBlocksMenu(self, menu):
        """
        Constructs the text blocks menu submenu and necessary subsubmenus.
        If this is called more than once, previously used menu ids are reused
        for the new menu.
        
        menu -- An empty wx.Menu to add items and submenus to
        """
        # Clear IdRecycler
        self.textBlocksActivation.clearAssoc()


        wikiDoc = self.getWikiDocument()
        if wikiDoc is not None and self.requireReadAccess():
            try:
                page = wikiDoc.getFuncPage(u"wiki/[TextBlocks]")
                treeData = TextTree.buildTreeFromText(page.getContent(),
                        TextTree.TextBlocksEntry.factory)
                TextTree.addTreeToMenu(treeData,
                        menu, self.textBlocksActivation, self,
                        self.OnTextBlockUsed)
                menu.AppendSeparator()

            except DbReadAccessError, e:
                self.lostReadAccess(e)
                traceback.print_exc()


        page = WikiDataManager.getGlobalFuncPage(u"global/[TextBlocks]")
        treeData = TextTree.buildTreeFromText(page.getContent(),
                TextTree.TextBlocksEntry.factory)
        TextTree.addTreeToMenu(treeData,
                menu, self.textBlocksActivation, self,
                self.OnTextBlockUsed)

        menu.AppendSeparator()
        menu.Append(GUI_ID.CMD_REREAD_TEXT_BLOCKS,
                _(u"Reread text blocks"),
                _(u"Reread the text block file(s) and recreate menu"))
        wx.EVT_MENU(self, GUI_ID.CMD_REREAD_TEXT_BLOCKS, self.OnRereadTextBlocks)


    def OnTextBlockUsed(self, evt):
        if self.isReadOnlyPage():
            return

        entry = self.textBlocksActivation.get(evt.GetId())

        if entry is None:
            return

        if u"a" in entry.flags:
            self.appendText(entry.value)
        else:
            self.addText(entry.value, replaceSel=True)


    
    def OnRereadTextBlocks(self, evt):
        self.rereadTextBlocks()
        
        
    def rereadTextBlocks(self):
        """
        Starts rereading and rebuilding of the text blocks submenu
        """
        if self.textBlocksMenu is None:
            return

        clearMenu(self.textBlocksMenu)
        self.fillTextBlocksMenu(self.textBlocksMenu)


    def fillFavoriteWikisMenu(self, menu):
        """
        Constructs the favorite wikis menu and necessary submenus.
        If this is called more than once, previously used menu ids are reused
        for the new menu.
        
        menu -- An empty wx.Menu to add items and submenus to
        """
        self.favoriteWikisActivation.clearAssoc()

        wikiDoc = self.getWikiDocument()

        page = WikiDataManager.getGlobalFuncPage(u"global/[FavoriteWikis]")
        treeData = TextTree.buildTreeFromText(page.getContent(),
                TextTree.FavoriteWikisEntry.factory)
        TextTree.addTreeToMenu(treeData,
                menu, self.favoriteWikisActivation, self,
                self.OnFavoriteWikiUsed)

        menu.AppendSeparator()
        menu.Append(GUI_ID.CMD_ADD_CURRENT_WIKI_TO_FAVORITES,
                _(u"Add wiki"),
                _(u"Add a wiki to the favorites"))
        wx.EVT_MENU(self, GUI_ID.CMD_ADD_CURRENT_WIKI_TO_FAVORITES,
                self.OnAddToFavoriteWikis)

        menu.Append(GUI_ID.CMD_MANAGE_FAVORITE_WIKIS,
                _(u"Manage favorites"),
                _(u"Manage favorites"))
        wx.EVT_MENU(self, GUI_ID.CMD_MANAGE_FAVORITE_WIKIS,
                self.OnManageFavoriteWikis)


    def OnFavoriteWikiUsed(self, evt):
        try:
            entry = self.favoriteWikisActivation.get(evt.GetId())

            if entry is None:
                return

            if u"n" in entry.flags:
                # Open in new frame
                try:
                    clAction = CmdLineAction([])
                    clAction.setWikiToOpen(entry.value)
                    clAction.frameToOpen = 1  # Open in new frame
                    wx.GetApp().startPersonalWikiFrame(clAction)
                except Exception, e:
                    traceback.print_exc()
                    self.displayErrorMessage(_(u'Error while starting new '
                            u'WikidPad instance'), e)
                    return
            else:
                # Open in same frame
                if entry.value.startswith(u"wiki:"):
                    # Handle an URL
                    filePath, wikiWordToOpen, anchorToOpen = \
                            wikiUrlToPathWordAndAnchor(entry.value)
                    if exists(pathEnc(filePath)):
                        self.openWiki(filePath, wikiWordsToOpen=(wikiWordToOpen,),
                                anchorToOpen=anchorToOpen)
                else:
                    self.openWiki(abspath(entry.value))

        except KeyError:
            pass


    def rereadFavoriteWikis(self):
        if self.favoriteWikisMenu is None:
            return

        clearMenu(self.favoriteWikisMenu)
        self.fillFavoriteWikisMenu(self.favoriteWikisMenu)
        
        # Update also toolbar by recreating
        if self.getShowToolbar():
            self.Freeze()
            try:
                self.setShowToolbar(False)
                self.setShowToolbar(True)
            finally:
                self.Thaw()


    def OnAddToFavoriteWikis(self,evt):
        document = self.getWikiDocument()
        if document is None:
            path = u""
            title = u""
        else:
            path = document.getWikiConfigPath()
            title = document.getWikiName()

        entry = TextTree.FavoriteWikisEntry(title, u"", u"",
                self._getStorableWikiPath(path))
        entry = TextTree.AddWikiToFavoriteWikisDialog.runModal(self, -1, entry)
        
        if entry is not None:
            page = WikiDataManager.getGlobalFuncPage(u"global/[FavoriteWikis]")
            text = page.getLiveText()
            if len(text) == 0 or text[-1] == u"\n":
                page.appendLiveText(entry.getTextLine() + u"\n")
            else:
                page.appendLiveText(u"\n" + entry.getTextLine() + u"\n")

            self.saveDocPage(page)


    def OnManageFavoriteWikis(self, evt):
        self.activatePageByUnifiedName(u"global/[FavoriteWikis]", tabMode=2)


    def OnInsertIconAttribute(self, evt):
        if self.isReadOnlyPage():
            return

        self.insertAttribute("icon", self.cmdIdToIconName[evt.GetId()])


    def OnInsertColorAttribute(self, evt):
        if self.isReadOnlyPage():
            return

        self.insertAttribute("color", self.cmdIdToColorName[evt.GetId()])


    def buildMainMenu(self):
        # ------------------------------------------------------------------------------------
        # Set up menu bar for the program.
        # ------------------------------------------------------------------------------------
        if self.mainmenu is not None:
            # This is a rebuild of an existing menu (after loading a new wikiData)
            self.mainmenu.Replace(0, self.buildWikiMenu(), 'W&iki')
            return


        self.mainmenu = wx.MenuBar()   # Create menu bar.

        wikiMenu = self.buildWikiMenu()

        wikiWordMenu=wx.Menu()

        self.addMenuItem(wikiWordMenu, _(u'&Open') + u'\t' + self.keyBindings.OpenWikiWord,
                _(u'Open Wiki Word'), lambda evt: self.showWikiWordOpenDialog(),
                "tb_doc")

        self.addMenuItem(wikiWordMenu, _(u'&Save') + u'\t' + self.keyBindings.Save,
                _(u'Save all open pages'),
                lambda evt: (self.saveAllDocPages(),
                self.getWikiData().commit()), "tb_save",
                menuID=GUI_ID.CMD_SAVE_WIKI,
                updatefct=self.OnUpdateDisReadOnlyWiki)

        # TODO More fine grained check for en-/disabling of rename and delete?
        self.addMenuItem(wikiWordMenu, _(u'&Rename') + u'\t' + self.keyBindings.Rename,
                _(u'Rename Current Wiki Word'), lambda evt: self.showWikiWordRenameDialog(),
                "tb_rename",
                menuID=GUI_ID.CMD_RENAME_PAGE,
                updatefct=(self.OnUpdateDisReadOnlyWiki, self.OnUpdateDisNotWikiPage))

        self.addMenuItem(wikiWordMenu, _(u'&Delete') + u'\t' + self.keyBindings.Delete,
                _(u'Delete Wiki Word'), lambda evt: self.showWikiWordDeleteDialog(),
                "tb_delete",
                menuID=GUI_ID.CMD_DELETE_PAGE,
                updatefct=(self.OnUpdateDisReadOnlyWiki, self.OnUpdateDisNotWikiPage))

        self.addMenuItem(wikiWordMenu, _(u'Add Bookmark') + u'\t' + self.keyBindings.AddBookmark,
                _(u'Add Bookmark to Page'), lambda evt: self.insertAttribute("bookmarked", "true"),
                "pin", updatefct=(self.OnUpdateDisReadOnlyWiki, self.OnUpdateDisNotWikiPage))

        if self.clipboardInterceptor is not None:
            wikiWordMenu.AppendSeparator()

            menuItem = wx.MenuItem(wikiWordMenu, GUI_ID.CMD_CLIPBOARD_CATCHER_AT_PAGE,
                    _(u'Clipboard Catcher at Page') + u'\t' + self.keyBindings.CatchClipboardAtPage, 
                    _(u"Text copied to clipboard is also appended to this page"),
                    wx.ITEM_RADIO)
            wikiWordMenu.AppendItem(menuItem)
            wx.EVT_MENU(self, GUI_ID.CMD_CLIPBOARD_CATCHER_AT_PAGE,
                    self.OnClipboardCatcherAtPage)
            wx.EVT_UPDATE_UI(self, GUI_ID.CMD_CLIPBOARD_CATCHER_AT_PAGE,
                    self.OnUpdateClipboardCatcher)


            menuItem = wx.MenuItem(wikiWordMenu, GUI_ID.CMD_CLIPBOARD_CATCHER_AT_CURSOR,
                    _(u'Clipboard Catcher at Cursor') + u'\t' + self.keyBindings.CatchClipboardAtCursor, 
                    _(u"Text copied to clipboard is also added to cursor position"),
                    wx.ITEM_RADIO)
            wikiWordMenu.AppendItem(menuItem)
            wx.EVT_MENU(self, GUI_ID.CMD_CLIPBOARD_CATCHER_AT_CURSOR,
                    self.OnClipboardCatcherAtCursor)
            wx.EVT_UPDATE_UI(self, GUI_ID.CMD_CLIPBOARD_CATCHER_AT_CURSOR,
                    self.OnUpdateClipboardCatcher)


            menuItem = wx.MenuItem(wikiWordMenu, GUI_ID.CMD_CLIPBOARD_CATCHER_OFF,
                    _(u'Clipboard Catcher off') + u'\t' + self.keyBindings.CatchClipboardOff, 
                    _(u"Switch off clipboard catcher"), wx.ITEM_RADIO)
            wikiWordMenu.AppendItem(menuItem)
            wx.EVT_MENU(self, GUI_ID.CMD_CLIPBOARD_CATCHER_OFF,
                    self.OnClipboardCatcherOff)
            wx.EVT_UPDATE_UI(self, GUI_ID.CMD_CLIPBOARD_CATCHER_OFF,
                    self.OnUpdateClipboardCatcher)


        wikiWordMenu.AppendSeparator()

#         menuID=wxNewId()
#         wikiWordMenu.Append(menuID, '&Activate Link/Word\t' + self.keyBindings.ActivateLink, 'Activate Link/Word')
#         EVT_MENU(self, menuID, lambda evt: self.activeEditor.activateLink())
# 
#         menuID=wxNewId()
#         wikiWordMenu.Append(menuID, '&View Parents\t' + self.keyBindings.ViewParents, 'View Parents Of Current Wiki Word')
#         EVT_MENU(self, menuID, lambda evt: self.viewParents(self.getCurrentWikiWord()))
# 
#         menuID=wxNewId()
#         wikiWordMenu.Append(menuID, 'View &Parentless Nodes\t' + self.keyBindings.ViewParentless, 'View nodes with no parent relations')
#         EVT_MENU(self, menuID, lambda evt: self.viewParentLess())
# 
#         menuID=wxNewId()
#         wikiWordMenu.Append(menuID, 'View &Children\t' + self.keyBindings.ViewChildren, 'View Children Of Current Wiki Word')
#         EVT_MENU(self, menuID, lambda evt: self.viewChildren(self.getCurrentWikiWord()))

        self.addMenuItem(wikiWordMenu, _(u'&Activate Link/Word') + u'\t' +
                self.keyBindings.ActivateLink, _(u'Activate link/word'),
                lambda evt: self.getActiveEditor().activateLink(),
                updatefct=(self.OnUpdateDisNotTextedit, self.OnUpdateDisNotWikiPage)
                ) # ,
#                 menuID=GUI_ID.CMD_ACTIVATE_LINK)

        self.addMenuItem(wikiWordMenu, _(u'Activate Link/&Word in new tab') + u'\t' +
                self.keyBindings.ActivateLinkNewTab, _(u'Activate link/word in new tab'),
                lambda evt: self.getActiveEditor().activateLink(tabMode=2),
                updatefct=(self.OnUpdateDisNotTextedit, self.OnUpdateDisNotWikiPage)
                )

        self.addMenuItem(wikiWordMenu, _(u'&List Parents') + u'\t' +
                self.keyBindings.ViewParents,
                _(u'View parents of current wiki word'),
                lambda evt: self.viewParents(self.getCurrentWikiWord()))

        self.addMenuItem(wikiWordMenu, _(u'List &Parentless Nodes') + u'\t' +
                self.keyBindings.ViewParentless,
                _(u'View nodes with no parent relations'),
                lambda evt: self.viewParentLess())

        self.addMenuItem(wikiWordMenu, _(u'List &Children') + u'\t' +
                self.keyBindings.ViewChildren,
                _(u'View children of current wiki word'),
                lambda evt: self.viewChildren(self.getCurrentWikiWord()))

        self.addMenuItem(wikiWordMenu, _(u'List &Bookmarks') + u'\t' +
                self.keyBindings.ViewBookmarks, _(u'View bookmarks'),
                lambda evt: self.viewBookmarks())

        self.addMenuItem(wikiWordMenu, _(u'Copy &URL to clipboard') + u'\t' +
                self.keyBindings.ClipboardCopyUrlToCurrentWikiword,
                _(u'Copy full "wiki:" URL of the word to clipboard'),
                self.OnCmdClipboardCopyUrlToCurrentWikiWord,
                updatefct=(self.OnUpdateDisNotWikiPage,))


        wikiWordMenu.AppendSeparator()

        self.addMenuItem(wikiWordMenu, _(u'Set As Roo&t') + u'\t' + self.keyBindings.SetAsRoot,
                _(u'Set current wiki word as tree root'),
                lambda evt: self.setCurrentWordAsRoot(),
                )

        self.addMenuItem(wikiWordMenu, _(u'R&eset Root') + u'\t' + self.keyBindings.ResetRoot,
                _(u'Set current wiki word as tree root'),
                lambda evt: self.setHomeWordAsRoot(),
                )

        self.addMenuItem(wikiWordMenu, _(u'S&ynchronize with tree'),
                _(u'Find the current wiki word in the tree'), lambda evt: self.findCurrentWordInTree(),
                "tb_cycle", updatefct=(self.OnUpdateDisNotWikiPage,))


        historyMenu = wx.Menu()


        self.addMenuItem(historyMenu, _(u'&List History') + u'\t' + self.keyBindings.ViewHistory,
                _(u'View History'), self._OnEventToCurrentDocPPresenter,
                menuID=GUI_ID.CMD_PAGE_HISTORY_LIST)

        self.addMenuItem(historyMenu, _(u'&Up History') + u'\t' + self.keyBindings.UpHistory,
                _(u'Up History'), self._OnEventToCurrentDocPPresenter,
                menuID=GUI_ID.CMD_PAGE_HISTORY_LIST_UP)

        self.addMenuItem(historyMenu, _(u'&Down History') + u'\t' + self.keyBindings.DownHistory,
                _(u'Down History'), self._OnEventToCurrentDocPPresenter,
                menuID=GUI_ID.CMD_PAGE_HISTORY_LIST_DOWN)

        self.addMenuItem(historyMenu, _(u'&Back') + u'\t' + self.keyBindings.GoBack,
                _(u'Go Back'), self._OnEventToCurrentDocPPresenter,
                "tb_back", menuID=GUI_ID.CMD_PAGE_HISTORY_GO_BACK)

        self.addMenuItem(historyMenu, _(u'&Forward') + u'\t' + self.keyBindings.GoForward,
                _(u'Go Forward'), self._OnEventToCurrentDocPPresenter,
                "tb_forward", menuID=GUI_ID.CMD_PAGE_HISTORY_GO_FORWARD)


        self.addMenuItem(historyMenu, _(u'&Wiki Home') + u'\t' + self.keyBindings.GoHome,
                _(u'Go to Wiki Home Page'),
                lambda evt: self.openWikiPage(self.getWikiDocument().getWikiName(),
                    forceTreeSyncFromRoot=True),
                "tb_home")


        editorMenu=wx.Menu()

        self.addMenuItem(editorMenu, _(u'&Bold') + u'\t' + self.keyBindings.Bold,
                _(u'Bold'), lambda evt: self.keyBindings.makeBold(self.getActiveEditor()),
                "tb_bold",
                menuID=GUI_ID.CMD_FORMAT_BOLD,
                updatefct=(self.OnUpdateDisReadOnlyPage, self.OnUpdateDisNotTextedit,
                    self.OnUpdateDisNotWikiPage))

        self.addMenuItem(editorMenu, _(u'&Italic') + u'\t' + self.keyBindings.Italic,
                _(u'Italic'), lambda evt: self.keyBindings.makeItalic(self.getActiveEditor()),
                "tb_italic",
                menuID=GUI_ID.CMD_FORMAT_ITALIC,
                updatefct=(self.OnUpdateDisReadOnlyPage, self.OnUpdateDisNotTextedit,
                    self.OnUpdateDisNotWikiPage))

        self.addMenuItem(editorMenu, _(u'&Heading') + u'\t' + self.keyBindings.Heading,
                _(u'Add Heading'), lambda evt: self.keyBindings.addHeading(self.getActiveEditor()),
                "tb_heading",
                menuID=GUI_ID.CMD_FORMAT_HEADING_PLUS,
                updatefct=(self.OnUpdateDisReadOnlyPage, self.OnUpdateDisNotTextedit,
                    self.OnUpdateDisNotWikiPage))

        self.addMenuItem(editorMenu, _(u'Insert Date') + u'\t' + self.keyBindings.InsertDate,
                _(u'Insert Date'), lambda evt: self.insertDate(),
                "date", updatefct=(self.OnUpdateDisReadOnlyPage, self.OnUpdateDisNotTextedit,
                    self.OnUpdateDisNotWikiPage))

        self.addMenuItem(editorMenu, _(u'Set Date Format'),
                _(u'Set Date Format'), lambda evt: self.showDateformatDialog())

        if SpellChecker.isSpellCheckSupported():
            self.addMenuItem(editorMenu, _(u'Spell check') + u'\t' + self.keyBindings.SpellCheck,
                    _(u'Spell check current page'),
                    lambda evt: self.showSpellCheckerDialog(),
                    updatefct=(self.OnUpdateDisReadOnlyPage, self.OnUpdateDisNotTextedit))

        self.addMenuItem(editorMenu,
                _(u'Wikize Selected Word') + u'\t' + self.keyBindings.MakeWikiWord,
                _(u'Wikize Selected Word'),
                lambda evt: self.keyBindings.makeWikiWord(self.getActiveEditor()),
                "pin", menuID=GUI_ID.CMD_FORMAT_WIKIZE_SELECTED,
                updatefct=(self.OnUpdateDisReadOnlyPage, self.OnUpdateDisNotTextedit))


        editorMenu.AppendSeparator()

        self.addMenuItem(editorMenu, _(u'Cu&t') + u'\t' + self.keyBindings.Cut,
                _(u'Cut'), self._OnRoundtripEvent,  # lambda evt: self.activeEditor.Cut(),
                "tb_cut", menuID=GUI_ID.CMD_CLIPBOARD_CUT,
                updatefct=(self.OnUpdateDisReadOnlyPage, self.OnUpdateDisNotTextedit))

#         self.addMenuItem(self.editorMenu, '&Copy\t' + self.keyBindings.Copy,
#                 'Copy', lambda evt: self.fireMiscEventKeys(("command copy",)), # lambda evt: self.activeEditor.Copy()
#                 "tb_copy", menuID=GUI_ID.CMD_CLIPBOARD_COPY)

        self.addMenuItem(editorMenu, _(u'&Copy') + u'\t' + self.keyBindings.Copy,
                _(u'Copy'), self._OnRoundtripEvent,  # lambda evt: self.activeEditor.Copy()
                "tb_copy", menuID=GUI_ID.CMD_CLIPBOARD_COPY)


        # TODO support copying from preview
        self.addMenuItem(editorMenu, _(u'Copy to &ScratchPad') + u'\t' + \
                self.keyBindings.CopyToScratchPad,
                _(u'Copy Text to ScratchPad'), lambda evt: self.getActiveEditor().snip(),
                "tb_copy", updatefct=self.OnUpdateDisReadOnlyWiki)

#         self.addMenuItem(self.editorMenu, '&Paste\t' + self.keyBindings.Paste,
#                 'Paste', lambda evt: self.activeEditor.Paste(),
#                 "tb_paste", menuID=GUI_ID.CMD_CLIPBOARD_PASTE)

        self.addMenuItem(editorMenu, _(u'&Paste') + u'\t' + self.keyBindings.Paste,
                _(u'Paste'), self._OnRoundtripEvent,  # lambda evt: self.activeEditor.Paste(),
                "tb_paste", menuID=GUI_ID.CMD_CLIPBOARD_PASTE,
                updatefct=(self.OnUpdateDisReadOnlyPage, self.OnUpdateDisNotTextedit))


        editorMenu.AppendSeparator()

        self.addMenuItem(editorMenu, _(u'&Undo') + u'\t' + self.keyBindings.Undo,
                _(u'Undo'), self._OnRoundtripEvent, menuID=GUI_ID.CMD_UNDO)

        self.addMenuItem(editorMenu, _(u'&Redo') + u'\t' + self.keyBindings.Redo,
                _(u'Redo'), self._OnRoundtripEvent, menuID=GUI_ID.CMD_REDO)

#         self.addMenuItem(editorMenu, '&Undo\t' + self.keyBindings.Undo,
#                 'Undo', lambda evt: self.activeEditor.CmdKeyExecute(wxSTC_CMD_UNDO))
# 
#         self.addMenuItem(editorMenu, '&Redo\t' + self.keyBindings.Redo,
#                 'Redo', lambda evt: self.activeEditor.CmdKeyExecute(wxSTC_CMD_REDO))


        editorMenu.AppendSeparator()

#         self.textBlocksMenuPosition = editorMenu.GetMenuItemCount()

        self.textBlocksMenu = wx.Menu()
        self.fillTextBlocksMenu(self.textBlocksMenu)

        editorMenu.AppendMenu(GUI_ID.MENU_TEXT_BLOCKS, _(u'&Text blocks'),
                self.textBlocksMenu)
        wx.EVT_UPDATE_UI(self, GUI_ID.MENU_TEXT_BLOCKS,
                self.OnUpdateDisReadOnlyPage)

        # Build icon menu
        if self.lowResources:
            # Add only menu item for icon select dialog
            self.addMenuItem(editorMenu, _(u'Add icon attribute'),
                    _(u'Open icon select dialog'),
                    lambda evt: self.showSelectIconDialog(),
                    updatefct=self.OnUpdateDisReadOnlyPage)
        else:
            # Build full submenu for icons
            iconsMenu, self.cmdIdToIconName = PropertyHandling.buildIconsSubmenu(
                    wx.GetApp().getIconCache())
            for cmi in self.cmdIdToIconName.keys():
                wx.EVT_MENU(self, cmi, self.OnInsertIconAttribute)

            editorMenu.AppendMenu(GUI_ID.MENU_ADD_ICON_ATTRIBUTE,
                    _(u'Add icon attribute'), iconsMenu)
            wx.EVT_UPDATE_UI(self, GUI_ID.MENU_ADD_ICON_ATTRIBUTE,
                    self.OnUpdateDisReadOnlyPage)


        # Build submenu for colors
        colorsMenu, self.cmdIdToColorName = PropertyHandling.buildColorsSubmenu()
        for cmi in self.cmdIdToColorName.keys():
            wx.EVT_MENU(self, cmi, self.OnInsertColorAttribute)

        editorMenu.AppendMenu(GUI_ID.MENU_ADD_COLOR_ATTRIBUTE,
                _(u'Add color attribute'), colorsMenu)
        wx.EVT_UPDATE_UI(self, GUI_ID.MENU_ADD_COLOR_ATTRIBUTE,
                self.OnUpdateDisReadOnlyPage)

        self.addMenuItem(editorMenu, _(u'Add &file URL') + '\t' + 
                self.keyBindings.AddFileUrl, _(u'Use file dialog to add URL'),
                lambda evt: self.showAddFileUrlDialog(),
                updatefct=(self.OnUpdateDisReadOnlyPage, self.OnUpdateDisNotTextedit))


        editorMenu.AppendSeparator()

#         menuID=wxNewId()
#         formattingMenu.Append(menuID, '&Find\t', 'Find')
#         EVT_MENU(self, menuID, lambda evt: self.showFindDialog())


        self.addMenuItem(editorMenu, _(u'Find and &Replace') + u'\t' + 
                self.keyBindings.FindAndReplace,
                _(u'Find and Replace'),
                lambda evt: self.showFindReplaceDialog())

        self.addMenuItem(editorMenu, _(u'Rep&lace Text by WikiWord') + u'\t' + 
                self.keyBindings.ReplaceTextByWikiword,
                _(u'Replace selected text by WikiWord'),
                lambda evt: self.showReplaceTextByWikiwordDialog(),
                updatefct=self.OnUpdateDisReadOnlyPage)

#         menuID=wx.NewId()
#         self.editorMenu.Append(menuID, 'Find and &Replace\t' + 
#                 self.keyBindings.FindAndReplace, 'Find and Replace')
#         wx.EVT_MENU(self, menuID, lambda evt: self.showFindReplaceDialog())
# 
#         menuID=wx.NewId()
#         self.editorMenu.Append(menuID, 'Rep&lace Text by WikiWord\t' + 
#                 self.keyBindings.ReplaceTextByWikiword, 'Replace selected text by WikiWord')
#         wx.EVT_MENU(self, menuID, lambda evt: self.showReplaceTextByWikiwordDialog())

        editorMenu.AppendSeparator()

        self.addMenuItem(editorMenu, _(u'&Rewrap Text') + u'\t' + 
                self.keyBindings.RewrapText,
                _(u'Rewrap Text'),
                lambda evt: self.getActiveEditor().rewrapText(),
                updatefct=self.OnUpdateDisReadOnlyPage)

#         menuID=wx.NewId()
#         editorMenu.Append(menuID, '&Rewrap Text\t' + 
#                 self.keyBindings.RewrapText, 'Rewrap Text')
#         wx.EVT_MENU(self, menuID, lambda evt: self.getActiveEditor().rewrapText())


        subMenu = wx.Menu()

        menuID=wx.NewId()
        wrapModeMenuItem = wx.MenuItem(subMenu, menuID, _(u"&Wrap Mode"),
                _(u"Set wrap mode"), wx.ITEM_CHECK)
        subMenu.AppendItem(wrapModeMenuItem)
        wx.EVT_MENU(self, menuID, self.OnCmdCheckWrapMode)
        wx.EVT_UPDATE_UI(self, menuID, self.OnUpdateWrapMode)


        menuID=wx.NewId()
        autoIndentMenuItem = wx.MenuItem(subMenu, menuID,
                _(u"Auto-indent"), _(u"Auto indentation"), wx.ITEM_CHECK)
        subMenu.AppendItem(autoIndentMenuItem)
        wx.EVT_MENU(self, menuID, self.OnCmdCheckAutoIndent)
        wx.EVT_UPDATE_UI(self, menuID, self.OnUpdateAutoIndent)


        menuID=wx.NewId()
        autoBulletsMenuItem = wx.MenuItem(subMenu, menuID,
                _(u"Auto-bullets"),
                _(u"Show bullet on next line if current has one"),
                wx.ITEM_CHECK)
        subMenu.AppendItem(autoBulletsMenuItem)
        wx.EVT_MENU(self, menuID, self.OnCmdCheckAutoBullets)
        wx.EVT_UPDATE_UI(self, menuID,
                self.OnUpdateAutoBullets)

        menuID=wx.NewId()
        autoBulletsMenuItem = wx.MenuItem(subMenu, menuID,
                _(u"Tabs to spaces"), _(u"Write spaces when hitting TAB key"),
                wx.ITEM_CHECK)
        subMenu.AppendItem(autoBulletsMenuItem)
        wx.EVT_MENU(self, menuID, self.OnCmdCheckTabsToSpaces)
        wx.EVT_UPDATE_UI(self, menuID,
                self.OnUpdateTabsToSpaces)


        editorMenu.AppendMenu(-1, _(u"Settings"), subMenu)

        editorMenu.AppendSeparator()


        evaluationMenu=wx.Menu()

        self.addMenuItem(evaluationMenu, _(u'&Eval') + u'\t' + self.keyBindings.Eval,
                _(u'Eval Script Blocks'),
                lambda evt: self.getActiveEditor().evalScriptBlocks())

        for i in xrange(1,7):
            self.addMenuItem(evaluationMenu,
                    (_(u'Eval Function &%i') + u'\tCtrl-%i') % (i, i),
                    _(u'Eval Script Function %i') % i,
                    lambda evt, i=i: self.getActiveEditor().evalScriptBlocks(i))
                    
        editorMenu.AppendMenu(wx.NewId(), _(u"Evaluation"), evaluationMenu,
                _(u"Evaluate scripts/expressions"))


        foldingMenu = wx.Menu()
        appendToMenuByMenuDesc(foldingMenu, FOLD_MENU, self.keyBindings)

        wx.EVT_MENU(self, GUI_ID.CMD_CHECKBOX_SHOW_FOLDING,
                self.OnCmdCheckShowFolding)
        wx.EVT_UPDATE_UI(self, GUI_ID.CMD_CHECKBOX_SHOW_FOLDING,
                self.OnUpdateShowFolding)


        wx.EVT_MENU(self, GUI_ID.CMD_TOGGLE_CURRENT_FOLDING,
                lambda evt: self.getActiveEditor().toggleCurrentFolding())
        wx.EVT_MENU(self, GUI_ID.CMD_UNFOLD_ALL_IN_CURRENT,
                lambda evt: self.getActiveEditor().unfoldAll())
        wx.EVT_MENU(self, GUI_ID.CMD_FOLD_ALL_IN_CURRENT,
                lambda evt: self.getActiveEditor().foldAll())




#         menuID=wx.NewId()
#         showFoldingMenuItem = wx.MenuItem(foldingMenu, menuID,
#                 "Show folding\t" + self.keyBindings.ShowFolding,
#                 "Show folding marks and allow folding",
#                 wx.ITEM_CHECK)
#         foldingMenu.AppendItem(showFoldingMenuItem)
#         wx.EVT_MENU(self, menuID, self.OnCmdCheckShowFolding)
#         wx.EVT_UPDATE_UI(self, menuID,
#                 self.OnUpdateShowFolding)
# 
#         self.addMenuItem(foldingMenu, '&Unfold All\t' +
#                 self.keyBindings.UnfoldAll,
#                 'Unfold everything in current editor',
#                 lambda evt: self.getActiveEditor().unfoldAll())
# 
#         self.addMenuItem(foldingMenu, '&Fold All\t' + self.keyBindings.FoldAll,
#                 'Fold everything in current editor',
#                 lambda evt: self.getActiveEditor().foldAll())

        viewMenu = wx.Menu()
        
        self.addMenuItem(viewMenu, _(u'Switch Ed./Prev') + u'\t' +
                self.keyBindings.ShowSwitchEditorPreview,
                _(u'Switch between editor and preview'),
                self.OnCmdSwitchEditorPreview,  "tb_switch ed prev",
                    menuID=GUI_ID.CMD_TAB_SHOW_SWITCH_EDITOR_PREVIEW)

        self.addMenuItem(viewMenu, _(u'Show Editor') + u'\t' + self.keyBindings.ShowEditor,
                _(u'Show Editor'),
                lambda evt: self.getCurrentDocPagePresenter().switchSubControl(
                    "textedit", gainFocus=True),  #  "tb_editor",
                    menuID=GUI_ID.CMD_TAB_SHOW_EDITOR)

        self.addMenuItem(viewMenu, _(u'Show Preview') + u'\t' +
                self.keyBindings.ShowPreview,
                _(u'Show Preview'),
                lambda evt: self.getCurrentDocPagePresenter().switchSubControl(
                    "preview", gainFocus=True),  #   "tb_preview",
                    menuID=GUI_ID.CMD_TAB_SHOW_PREVIEW)



        viewMenu.AppendSeparator()

        self.addMenuItem(viewMenu, _(u'&Zoom In') + u'\t' + self.keyBindings.ZoomIn,
                _(u'Zoom In'), self._OnRoundtripEvent, "tb_zoomin",
                menuID=GUI_ID.CMD_ZOOM_IN)

        self.addMenuItem(viewMenu, _(u'Zoo&m Out') + u'\t' + self.keyBindings.ZoomOut,
                _(u'Zoom Out'), self._OnRoundtripEvent, "tb_zoomout",
                menuID=GUI_ID.CMD_ZOOM_OUT)

        viewMenu.AppendSeparator()


        menuID = wx.NewId()
        menuItem = wx.MenuItem(viewMenu, menuID,
                _(u'&Show Tree Control') + u'\t' + self.keyBindings.ShowTreeControl,
                _(u"Show Tree Control"), wx.ITEM_CHECK)
        viewMenu.AppendItem(menuItem)
        wx.EVT_MENU(self, menuID, lambda evt: self.setShowTreeControl(
                self.windowLayouter.isWindowCollapsed("maintree")))
        wx.EVT_UPDATE_UI(self, menuID, self.OnUpdateTreeCtrlMenuItem)

        menuItem = wx.MenuItem(viewMenu, GUI_ID.CMD_SHOW_TOOLBAR,
                _(u'Show Toolbar') + u'\t' + self.keyBindings.ShowToolbar, 
                _(u"Show Toolbar"), wx.ITEM_CHECK)
        viewMenu.AppendItem(menuItem)
        wx.EVT_MENU(self, GUI_ID.CMD_SHOW_TOOLBAR, lambda evt: self.setShowToolbar(
                not self.getConfig().getboolean("main", "toolbar_show", True)))
        wx.EVT_UPDATE_UI(self, GUI_ID.CMD_SHOW_TOOLBAR,
                self.OnUpdateToolbarMenuItem)

        menuID = wx.NewId()
        menuItem = wx.MenuItem(viewMenu, menuID,
                _(u'Show &Doc. Structure') + u'\t' + self.keyBindings.ShowDocStructure,
                _(u"Show Document Structure"), wx.ITEM_CHECK)
        viewMenu.AppendItem(menuItem)
        wx.EVT_MENU(self, menuID, lambda evt: self.setShowDocStructure(
                self.windowLayouter.isWindowCollapsed("doc structure")))
        wx.EVT_UPDATE_UI(self, menuID, self.OnUpdateDocStructureMenuItem)

        menuID = wx.NewId()
        menuItem = wx.MenuItem(viewMenu, menuID,
                _(u'&Show Time View') + u'\t' + self.keyBindings.ShowTimeView,
                _(u"Show Time View"), wx.ITEM_CHECK)
        viewMenu.AppendItem(menuItem)
        wx.EVT_MENU(self, menuID, lambda evt: self.setShowTimeView(
                self.windowLayouter.isWindowCollapsed("time view")))
        wx.EVT_UPDATE_UI(self, menuID, self.OnUpdateTimeViewMenuItem)

        menuItem = wx.MenuItem(viewMenu, GUI_ID.CMD_STAY_ON_TOP,
                _(u'Stay on Top') + u'\t' + self.keyBindings.StayOnTop, 
                _(u"Stay on Top"), wx.ITEM_CHECK)
        viewMenu.AppendItem(menuItem)
        wx.EVT_MENU(self, GUI_ID.CMD_STAY_ON_TOP, lambda evt: self.setStayOnTop(
                not self.getStayOnTop()))
        wx.EVT_UPDATE_UI(self, GUI_ID.CMD_STAY_ON_TOP,
                self.OnUpdateStayOnTopMenuItem)


        menuID=wx.NewId()
        indentGuidesMenuItem = wx.MenuItem(viewMenu, menuID,
                _(u"&View Indentation Guides"), _(u"View Indentation Guides"),
                wx.ITEM_CHECK)
        viewMenu.AppendItem(indentGuidesMenuItem)
        wx.EVT_MENU(self, menuID, self.OnCmdCheckIndentationGuides)
        wx.EVT_UPDATE_UI(self, menuID, self.OnUpdateIndentationGuides)

#         indentGuidesMenuItem.Check(self.getActiveEditor().GetIndentationGuides())

        menuID=wx.NewId()
        showLineNumbersMenuItem = wx.MenuItem(viewMenu, menuID,
                _(u"Show line numbers"), _(u"Show line numbers"),
                wx.ITEM_CHECK)
        viewMenu.AppendItem(showLineNumbersMenuItem)
        wx.EVT_MENU(self, menuID, self.OnCmdCheckShowLineNumbers)
        wx.EVT_UPDATE_UI(self, menuID, self.OnUpdateShowLineNumbers)

#         showLineNumbersMenuItem.Check(self.getActiveEditor().getShowLineNumbers())

        viewMenu.AppendSeparator()

        self.addMenuItem(viewMenu, _(u'Clone Window') + u'\t' +
                self.keyBindings.CloneWindow,
                _(u'Create new window for same wiki'), self.OnCmdCloneWindow)


        helpMenu = wx.Menu()

        def openHelp(evt):
            try:
                clAction = CmdLineAction([])
                clAction.wikiToOpen = self.wikiPadHelp
                clAction.frameToOpen = 1  # Open in new frame

                wx.GetApp().startPersonalWikiFrame(clAction)
            except Exception, e:
                traceback.print_exc()
                self.displayErrorMessage(_(u'Error while starting new '
                        u'WikidPad instance'), e)
                return


        menuID=wx.NewId()
        helpMenu.Append(menuID, _(u'&Open WikidPadHelp'), _(u'Open WikidPadHelp'))
        wx.EVT_MENU(self, menuID, openHelp)

        helpMenu.AppendSeparator()

        menuID=wx.NewId()
        helpMenu.Append(menuID, _(u'&Visit wikidPad Homepage'), _(u'Visit Homepage'))
        wx.EVT_MENU(self, menuID, lambda evt: OsAbstract.startFile(self, HOMEPAGE))

        helpMenu.AppendSeparator()

        menuID = wx.NewId()
        helpMenu.Append(menuID, _(u'View &License'), _(u'View License'))
        wx.EVT_MENU(self, menuID, lambda evt: OsAbstract.startFile(self, 
                join(self.wikiAppDir, u'license.txt')))

        if wx.Platform != "__WXMAC__":
            #don't need final separator if about item is going to app menu
            helpMenu.AppendSeparator()

        menuID = wx.ID_ABOUT
        helpMenu.Append(menuID, _(u'&About'), _(u'About WikidPad'))
        wx.EVT_MENU(self, menuID, lambda evt: self.showAboutDialog())

        self.mainmenu.Append(wikiMenu, _(u'W&iki'))
        self.mainmenu.Append(wikiWordMenu, _(u'&Wiki Words'))
        self.mainmenu.Append(historyMenu, _(u'&History'))
        self.mainmenu.Append(editorMenu, _(u'&Editor'))
        self.mainmenu.Append(foldingMenu, _(u'&Folding'))
        self.mainmenu.Append(viewMenu, _(u'&View'))
        self.favoriteWikisMenu = wx.Menu()
        self.fillFavoriteWikisMenu(self.favoriteWikisMenu)
        self.mainmenu.Append(self.favoriteWikisMenu, _(u"F&avorites"))
#         if pluginMenu:
#         self.mainmenu.Append(pluginMenu, "Pl&ugins")
        self.pluginsMenu = wx.Menu()
        self.fillPluginsMenu(self.pluginsMenu)
        self.mainmenu.Append(self.pluginsMenu, _(u"Pl&ugins"))


        #Mac does not use menu accellerators anyway and wx special cases &Help to the in build Help menu
        #this check stops 2 help menus on mac
        if wx.Platform == "__WXMAC__": 
            self.mainmenu.Append(helpMenu, _(u'&Help'))
        else:
            self.mainmenu.Append(helpMenu, _(u'He&lp'))

        self.SetMenuBar(self.mainmenu)

        if self.getWikiConfigPath():  # If a wiki is open
            self.mainmenu.EnableTop(1, 1)
            self.mainmenu.EnableTop(2, 1)
            self.mainmenu.EnableTop(3, 1)
        else:
            self.mainmenu.EnableTop(1, 0)
            self.mainmenu.EnableTop(2, 0)
            self.mainmenu.EnableTop(3, 0)



    def buildToolbar(self):
        # ------------------------------------------------------------------------------------
        # Create the toolbar
        # ------------------------------------------------------------------------------------

        tb = self.CreateToolBar(wx.TB_HORIZONTAL | wx.NO_BORDER | wx.TB_FLAT | wx.TB_TEXT)
        seperator = self.lookupSystemIcon("tb_seperator")

        icon = self.lookupSystemIcon("tb_back")
        tbID = GUI_ID.CMD_PAGE_HISTORY_GO_BACK
        tb.AddSimpleTool(tbID, icon, _(u"Back") + " " + self.keyBindings.GoBack,
                _(u"Back"))
        wx.EVT_TOOL(self, tbID, self._OnEventToCurrentDocPPresenter)

        icon = self.lookupSystemIcon("tb_forward")
        tbID = GUI_ID.CMD_PAGE_HISTORY_GO_FORWARD
        tb.AddSimpleTool(tbID, icon, _(u"Forward") + " " + self.keyBindings.GoForward,
                _(u"Forward"))
        wx.EVT_TOOL(self, tbID, self._OnEventToCurrentDocPPresenter)

        icon = self.lookupSystemIcon("tb_home")
        tbID = wx.NewId()
        tb.AddSimpleTool(tbID, icon, _(u"Wiki Home") + " " + self.keyBindings.GoHome,
                _(u"Wiki Home"))
        wx.EVT_TOOL(self, tbID,
                lambda evt: self.openWikiPage(self.getWikiDocument().getWikiName(),
                forceTreeSyncFromRoot=True))

        icon = self.lookupSystemIcon("tb_doc")
        tbID = wx.NewId()
        tb.AddSimpleTool(tbID, icon,
                _(u"Open Wiki Word") + " " + self.keyBindings.OpenWikiWord,
                _(u"Open Wiki Word"))
        wx.EVT_TOOL(self, tbID, lambda evt: self.showWikiWordOpenDialog())

        icon = self.lookupSystemIcon("tb_lens")
        tbID = wx.NewId()
        tb.AddSimpleTool(tbID, icon, _(u"Search") + " " + self.keyBindings.SearchWiki,
                _(u"Search"))
        wx.EVT_TOOL(self, tbID, lambda evt: self.showSearchDialog())

        icon = self.lookupSystemIcon("tb_cycle")
        tbID = wx.NewId()
        tb.AddSimpleTool(tbID, icon, _(u"Find current word in tree"),
                _(u"Find current word in tree"))
        wx.EVT_TOOL(self, tbID, lambda evt: self.findCurrentWordInTree())

        tb.AddSimpleTool(wx.NewId(), seperator, _(u"Separator"), _(u"Separator"))

        icon = self.lookupSystemIcon("tb_save")
        tb.AddSimpleTool(GUI_ID.CMD_SAVE_WIKI, icon,
                _(u"Save Wiki Word") + " " + self.keyBindings.Save,
                _(u"Save Wiki Word"))
#         wx.EVT_TOOL(self, GUI_ID.CMD_SAVE_WIKI,
#                 lambda evt: (self.saveAllDocPages(force=True),
#                 self.getWikiData().commit()))

        icon = self.lookupSystemIcon("tb_rename")
#         tbID = wx.NewId()
        tb.AddSimpleTool(GUI_ID.CMD_RENAME_PAGE, icon,
                _(u"Rename Wiki Word") + " " + self.keyBindings.Rename,
                _(u"Rename Wiki Word"))
#         wx.EVT_TOOL(self, tbID, lambda evt: self.showWikiWordRenameDialog())

        icon = self.lookupSystemIcon("tb_delete")
#         tbID = wx.NewId()
        tb.AddSimpleTool(GUI_ID.CMD_DELETE_PAGE, icon,
                _(u"Delete") + " " + self.keyBindings.Delete, _(u"Delete Wiki Word"))
#         wx.EVT_TOOL(self, tbID, lambda evt: self.showWikiWordDeleteDialog())

        tb.AddSimpleTool(wx.NewId(), seperator, _(u"Separator"), _(u"Separator"))

        icon = self.lookupSystemIcon("tb_heading")
#         tbID = wx.NewId()
        tb.AddSimpleTool(GUI_ID.CMD_FORMAT_HEADING_PLUS, icon,
                _(u"Heading") + " " + self.keyBindings.Heading, _(u"Heading"))
#         wx.EVT_TOOL(self, tbID, lambda evt: self.keyBindings.addHeading(
#                 self.getActiveEditor()))

        icon = self.lookupSystemIcon("tb_bold")
#         tbID = wx.NewId()
        tb.AddSimpleTool(GUI_ID.CMD_FORMAT_BOLD, icon,
                _(u"Bold") + " " + self.keyBindings.Bold, _(u"Bold"))
#         wx.EVT_TOOL(self, tbID, lambda evt: self.keyBindings.makeBold(
#                 self.getActiveEditor()))

        icon = self.lookupSystemIcon("tb_italic")
#         tbID = wx.NewId()
        tb.AddSimpleTool(GUI_ID.CMD_FORMAT_ITALIC, icon,
                _(u"Italic") + " " + self.keyBindings.Italic, _(u"Italic"))
#         wx.EVT_TOOL(self, tbID, lambda evt: self.keyBindings.makeItalic(
#                 self.getActiveEditor()))

        tb.AddSimpleTool(wx.NewId(), seperator, _(u"Separator"), _(u"Separator"))

#         icon = self.lookupSystemIcon("tb_editor")
#         tbID = GUI_ID.CMD_TAB_SHOW_EDITOR
#         tb.AddSimpleTool(tbID, icon, "Show Editor", "Show Editor")
# 
#         icon = self.lookupSystemIcon("tb_preview")
#         tbID = GUI_ID.CMD_TAB_SHOW_PREVIEW
#         tb.AddSimpleTool(tbID, icon, "Show Preview", "Show Preview")

        icon = self.lookupSystemIcon("tb_switch ed prev")
        tbID = GUI_ID.CMD_TAB_SHOW_SWITCH_EDITOR_PREVIEW
        tb.AddSimpleTool(tbID, icon, _(u"Switch Editor/Preview"),
                _(u"Switch between editor and preview"))

        icon = self.lookupSystemIcon("tb_zoomin")
        tbID = GUI_ID.CMD_ZOOM_IN
        tb.AddSimpleTool(tbID, icon, _(u"Zoom In"), _(u"Zoom In"))
        wx.EVT_TOOL(self, tbID, self._OnRoundtripEvent)

        icon = self.lookupSystemIcon("tb_zoomout")
        tbID = GUI_ID.CMD_ZOOM_OUT
        tb.AddSimpleTool(tbID, icon, _(u"Zoom Out"), _(u"Zoom Out"))
        wx.EVT_TOOL(self, tbID, self._OnRoundtripEvent)


        self.fastSearchField = wx.TextCtrl(tb, GUI_ID.TF_FASTSEARCH,
                style=wx.TE_PROCESS_ENTER | wx.TE_RICH)
        tb.AddControl(self.fastSearchField)
        wx.EVT_KEY_DOWN(self.fastSearchField, self.OnFastSearchKeyDown)

        icon = self.lookupSystemIcon("pin")
#         tbID = wx.NewId()
        tb.AddSimpleTool(GUI_ID.CMD_FORMAT_WIKIZE_SELECTED, icon,
                _(u"Wikize Selected Word ") + self.keyBindings.MakeWikiWord,
                _(u"Wikize Selected Word"))
#         wx.EVT_TOOL(self, tbID, lambda evt: self.keyBindings.makeWikiWord(self.getActiveEditor()))

#         for menuID, entryData in self.favoriteWikisActivation.iteritems():
#             entryFlags, entryValue = entryData

        # Build favorite wikis tool buttons
        wikiDoc = self.getWikiDocument()
        page = WikiDataManager.getGlobalFuncPage(u"global/[FavoriteWikis]")
        treeData = TextTree.buildTreeFromText(page.getContent(),
                TextTree.FavoriteWikisEntry.factory)
        
        toolEntries = [(None, None)] * 9

        # Filter entries from activation map with a digit (1 to 9) in the flags.
        # This digit defines the position in the toolbar.
        for menuID, entry in self.favoriteWikisActivation.iteritems():
            num = entry.getToolbarPosition()
            if num != -1:
                toolEntries[num - 1] = (menuID, entry)

        defIcon = self.lookupSystemIcon("tb_doc")

        # Now go through found entries to create tool buttons
        for menuID, entry in toolEntries:
            if entry is None:
                # No entry for this digit
                continue

            icon = self.resolveIconDescriptor(entry.iconDesc, defIcon)
            tbID = menuID
            tb.AddSimpleTool(tbID, icon, entry.title, entry.value)
#             wx.EVT_TOOL(self, tbID, self._OnRoundtripEvent)   # TODO Check if needed on Linux/GTK


        # get info for any plugin toolbar items and create them as necessary
        toolbarItems = reduce(lambda a, b: a+list(b),
                self.toolbarFunctions.describeToolbarItems(self), [])
        
        def addPluginTool(function, tooltip, statustext, icondesc, tbID=None,
                updateFunction=None):
            if tbID is None:
                tbID = wx.NewId()
                
            icon = self.resolveIconDescriptor(icondesc, defIcon)
            # tb.AddLabelTool(tbID, label, icon, wxNullBitmap, 0, tooltip)
            tb.AddSimpleTool(tbID, icon, tooltip, statustext)
            wx.EVT_TOOL(self, tbID, lambda evt: function(self, evt))

            if updateFunction is not None:
                wx.EVT_UPDATE_UI(self, tbID, lambda evt: updateFunction(self, evt))


        for item in toolbarItems:
            addPluginTool(*item)


        tb.Realize()



    def initializeGui(self):
        "initializes the gui environment"

        # ------------------------------------------------------------------------------------
        # Create the status bar
        # ------------------------------------------------------------------------------------
        self.statusBar = wx.StatusBar(self, -1)
        self.statusBar.SetFieldsCount(3)

        # Measure necessary widths of status fields
        dc = wx.ClientDC(self.statusBar)
        try:
            dc.SetFont(self.statusBar.GetFont())
            posWidth = dc.GetTextExtent(
                    _(u"Line: 9999 Col: 9999 Pos: 9999999988888"))[0]
            dc.SetFont(wx.NullFont)
        finally:
            del dc

        
        # Create main area panel first
        self.mainAreaPanel = MainAreaPanel(self, self, -1)
#         self.mainAreaPanel = MainAreaPanel(self)
        self.mainAreaPanel.getMiscEvent().addListener(self)

        p = self.createNewDocPagePresenterTab()
        self.mainAreaPanel.setCurrentDocPagePresenter(p)
 
        # Build layout:

        self.windowLayouter = WindowSashLayouter(self, self.createWindow)

        cfstr = self.getConfig().get("main", "windowLayout")
        self.windowLayouter.setWinPropsByConfig(cfstr)
       
        self.windowLayouter.realize()
#         self.windowLayouter.layout()

        self.tree = self.windowLayouter.getWindowForName("maintree")
        self.logWindow = self.windowLayouter.getWindowForName("log")


        

#         wx.EVT_NOTEBOOK_PAGE_CHANGED(self, self.mainAreaPanel.GetId(),
#                 self.OnNotebookPageChanged)
#         wx.EVT_CONTEXT_MENU(self.mainAreaPanel, self.OnNotebookContextMenu)
# 
#         wx.EVT_SET_FOCUS(self.mainAreaPanel, self.OnNotebookFocused)


        # ------------------------------------------------------------------------------------
        # Create menu and toolbar
        # ------------------------------------------------------------------------------------
        
        self.buildMainMenu()
        if self.getConfig().getboolean("main", "toolbar_show", True):
            self.setShowToolbar(True)

        wx.EVT_MENU(self, GUI_ID.CMD_SWITCH_FOCUS, self.OnSwitchFocus)

        # Table with additional possible accelerators
        ADD_ACCS = (
                ("CloseCurrentTab", GUI_ID.CMD_CLOSE_CURRENT_TAB),
                ("SwitchFocus", GUI_ID.CMD_SWITCH_FOCUS),
                ("GoNextTab", GUI_ID.CMD_GO_NEXT_TAB),
                ("GoPreviousTab", GUI_ID.CMD_GO_PREVIOUS_TAB),
                ("FocusFastSearchField", GUI_ID.CMD_FOCUS_FAST_SEARCH_FIELD)
#                 ("ActivateLink2", GUI_ID.CMD_ACTIVATE_LINK)
                )


        # Add alternative accelerators for clipboard operations
        accs = [
                (wx.ACCEL_CTRL, wx.WXK_INSERT, GUI_ID.CMD_CLIPBOARD_COPY),
                (wx.ACCEL_SHIFT, wx.WXK_INSERT, GUI_ID.CMD_CLIPBOARD_PASTE),
                (wx.ACCEL_SHIFT, wx.WXK_DELETE, GUI_ID.CMD_CLIPBOARD_CUT)
                ]


        # Add additional accelerators
        for keyName, menuId in ADD_ACCS:
            accP = self.keyBindings.getAccelPair(keyName)
            if accP != (None, None):
                accs.append((accP[0], accP[1], menuId))

        if Configuration.isLinux():   # Actually if wxGTK
            accs += [(wx.ACCEL_NORMAL, fkey, GUI_ID.SPECIAL_EAT_KEY)
                    for fkey in range(wx.WXK_F1, wx.WXK_F24 + 1)] + \
                    [(wx.ACCEL_SHIFT, fkey, GUI_ID.SPECIAL_EAT_KEY)
                    for fkey in range(wx.WXK_F1, wx.WXK_F24 + 1)]
    
            wx.EVT_MENU(self, GUI_ID.SPECIAL_EAT_KEY, lambda evt: None)

        self.SetAcceleratorTable(wx.AcceleratorTable(accs))

        # Check if window should stay on top
        self.setStayOnTop(self.getConfig().getboolean("main", "frame_stayOnTop",
                False))

        self.statusBar.SetStatusWidths([-1, -1, posWidth])
        self.SetStatusBar(self.statusBar)

        # Register the App IDLE handler
        wx.EVT_IDLE(self, self.OnIdle)

        # Register the App close handler
        wx.EVT_CLOSE(self, self.OnCloseButton)

#         # Check resizing to layout sash windows
        wx.EVT_SIZE(self, self.OnSize)

        wx.EVT_ICONIZE(self, self.OnIconize)
        wx.EVT_MAXIMIZE(self, self.OnMaximize)
        
        wx.EVT_MENU(self, GUI_ID.CMD_CLOSE_CURRENT_TAB, self._OnRoundtripEvent)
        wx.EVT_MENU(self, GUI_ID.CMD_GO_NEXT_TAB, self._OnRoundtripEvent)
        wx.EVT_MENU(self, GUI_ID.CMD_GO_PREVIOUS_TAB, self._OnRoundtripEvent)
        wx.EVT_MENU(self, GUI_ID.CMD_FOCUS_FAST_SEARCH_FIELD,
                self.OnCmdFocusFastSearchField)


    def OnUpdateTreeCtrlMenuItem(self, evt):
        evt.Check(not self.windowLayouter.isWindowCollapsed("maintree"))

    def OnUpdateToolbarMenuItem(self, evt):
        evt.Check(not self.GetToolBar() is None)

    def OnUpdateDocStructureMenuItem(self, evt):
        evt.Check(not self.windowLayouter.isWindowCollapsed("doc structure"))

    def OnUpdateTimeViewMenuItem(self, evt):
        evt.Check(not self.windowLayouter.isWindowCollapsed("time view"))

    def OnUpdateStayOnTopMenuItem(self, evt):
        evt.Check(self.getStayOnTop())


    def OnSwitchFocus(self, evt):
        foc = wx.Window.FindFocus()
        mainAreaPanel = self.mainAreaPanel
        while foc != None:
            if foc == mainAreaPanel:
                self.tree.SetFocus()
                return
            
            foc = foc.GetParent()
            
        mainAreaPanel.SetFocus()


    def OnFastSearchKeyDown(self, evt):
        """
        Process wx.EVT_KEY_DOWN in the fast search text field
        """
        acc = getAccelPairFromKeyDown(evt)
        if acc == (wx.ACCEL_NORMAL, wx.WXK_RETURN) or \
                acc == (wx.ACCEL_NORMAL, wx.WXK_NUMPAD_ENTER):
            text = guiToUni(self.fastSearchField.GetValue())
            tfHeight = self.fastSearchField.GetSize()[1]
            pos = self.fastSearchField.ClientToScreen((0, tfHeight))

            popup = FastSearchPopup(self, self, -1, pos=pos)
            popup.Show()
            try:
                popup.runSearchOnWiki(text)
            except re.error, e:
                popup.Show(False)
                self.displayErrorMessage(_(u'Regular expression error'), e)
        else:
            evt.Skip()

#     def OnFastSearchChar(self, evt):
#         print "OnFastSearchChar", repr(evt.GetUnicodeKey()), repr(evt.GetKeyCode())
#         evt.Skip()

    def OnCmdReconnectDatabase(self, evt):
        result = wx.MessageBox(_(u"Are you sure you want to reconnect? "
                u"You may lose some data by this process."),
                _(u'Reconnect database'),
                wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION, self)

        wd = self.getWikiDocument()
        if result == wx.YES and wd is not None:
            wd.setReadAccessFailed(True)
            wd.setWriteAccessFailed(True)
            # Try reading
            while True:
                try:
                    wd.reconnect()
                    wd.setReadAccessFailed(False)
                    break   # Success
                except (IOError, OSError, DbAccessError), e:
                    sys.stderr.write(_(u"Error while trying to reconnect:\n"))
                    traceback.print_exc()
                    result = wx.MessageBox(uniToGui(_(
                            u'There was an error while reconnecting the database\n\n'
                            u'Would you like to try it again?\n%s') %
                            e), _(u'Error reconnecting!'),
                            wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION, self)
                    if result == wx.NO:
                        return

            # Try writing
            while True:
                try:
                    # write out the current configuration
                    self.writeCurrentConfig()
                    self.getWikiData().testWrite()

                    wd.setNoAutoSaveFlag(False)
                    wd.setWriteAccessFailed(False)
                    break   # Success
                except (IOError, OSError, DbWriteAccessError), e:
                    sys.stderr.write(_(u"Error while trying to write:\n"))
                    traceback.print_exc()
                    result = wx.MessageBox(uniToGui(_(
                            u'There was an error while writing to the database\n\n'
                            u'Would you like to try it again?\n%s') %
                            e), _(u'Error writing!'),
                            wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION, self)
                    if result == wx.NO:
                        break

                
    def OnRemoteCommand(self, evt):
        try:
            clAction = CmdLineAction(evt.getCmdLine())
            wx.GetApp().startPersonalWikiFrame(clAction)
        except Exception, e:
            traceback.print_exc()
            self.displayErrorMessage(_(u'Error while starting new '
                    u'WikidPad instance'), e)
            return


    def OnShowHideHotkey(self, evt):
        if self.IsActive():
            self.Iconize(True)
        else:
            if self.IsIconized():
                self.Iconize(False)
                self.Show(True)

            self.Raise()


    def OnCmdFocusFastSearchField(self, evt):
        if self.fastSearchField is not None:
            self.fastSearchField.SetFocus()


    def OnCmdClipboardCopyUrlToCurrentWikiWord(self, evt):
        wikiWord = self.getCurrentWikiWord()
        if wikiWord is None:
            return
        
        path = self.getWikiDocument().getWikiConfigPath()
        copyTextToClipboard(pathWordAndAnchorToWikiUrl(path, wikiWord, None))


    def goBrowserBack(self):
        evt = wx.CommandEvent(wx.wxEVT_COMMAND_MENU_SELECTED,
                GUI_ID.CMD_PAGE_HISTORY_GO_BACK)
        self._OnEventToCurrentDocPPresenter(evt)

    def goBrowserForward(self):
        evt = wx.CommandEvent(wx.wxEVT_COMMAND_MENU_SELECTED,
                GUI_ID.CMD_PAGE_HISTORY_GO_FORWARD)
        self._OnEventToCurrentDocPPresenter(evt)


    def _refreshHotKeys(self):
        """
        Refresh the system-wide hotkey settings according to configuration
        """
        # A dummy window must be destroyed and recreated because
        # Unregistering a hotkey doesn't work
        if self.hotKeyDummyWindow is not None:
            self.hotKeyDummyWindow.Destroy()

        self.hotKeyDummyWindow = DummyWindow(self, id=GUI_ID.WND_HOTKEY_DUMMY)
        if self.configuration.getboolean("main",
                "hotKey_showHide_byApp_isActive"):
            setHotKeyByString(self.hotKeyDummyWindow,
                    self.HOTKEY_ID_HIDESHOW_BYAPP,
                    self.configuration.get("main",
                    "hotKey_showHide_byApp", u""))

        if self.getWikiDocument() is not None:
            setHotKeyByString(self.hotKeyDummyWindow,
                    self.HOTKEY_ID_HIDESHOW_BYWIKI,
                    self.configuration.get("main",
                    "hotKey_showHide_byWiki", u""))
        wx.EVT_HOTKEY(self.hotKeyDummyWindow, self.HOTKEY_ID_HIDESHOW_BYAPP,
                self.OnShowHideHotkey)
        wx.EVT_HOTKEY(self.hotKeyDummyWindow, self.HOTKEY_ID_HIDESHOW_BYWIKI,
                self.OnShowHideHotkey)


    def createWindow(self, winProps, parent):
        """
        Creates tree, editor, splitter, ... according to the given window name
        in winProps
        """
        winName = winProps["name"]
        if winName == "maintree" or winName == "viewstree":
            tree = WikiTreeCtrl(self, parent, -1, winName[:-4])
            # assign the image list
            try:
                # For native wx tree:
                # tree.AssignImageList(wx.GetApp().getIconCache().getNewImageList())
                # For custom tree control:
                tree.SetImageListNoGrayedItems(
                        wx.GetApp().getIconCache().getImageList())
            except Exception, e:
                traceback.print_exc()
                self.displayErrorMessage(_(u'There was an error loading the icons '
                        'for the tree control.'), e)
            if self.getWikiConfigPath() is not None and winName == "viewstree":
                tree.setViewsAsRoot()
                tree.expandRoot()
            return tree
        elif winName.startswith("txteditor"):
            editor = WikiTxtCtrl(winProps["presenter"], parent, -1)
            editor.evalScope = { 'editor' : editor,
                    'pwiki' : self, 'lib': self.evalLib}

            # enable and zoom the editor
            editor.Enable(0)
            editor.SetZoom(self.configuration.getint("main", "zoom"))
            return editor
        elif winName == "log":
            return LogWindow(parent, -1, self)
        elif winName == "doc structure":
            return DocStructureCtrl(parent, -1, self)
        elif winName == "time view":
            return TimeViewCtrl(parent, -1, self)
        elif winName == "main area panel":  # TODO remove this hack
            self.mainAreaPanel.Reparent(parent)
                
#             if not self._mainAreaPanelCreated:
#                 print "--Parent main area panel2", repr(parent)
#                 self.mainAreaPanel.Create(parent, -1)
#                 self._mainAreaPanelCreated = True

#             self.mainAreaPanel.Reparent(parent)
#             self.mainAreaPanel = MainAreaPanel(parent, self, -1)
#             self.mainAreaPanel.getMiscEvent().addListener(self)
# 
#             p = self.createNewDocPagePresenterTab()
#             self.mainAreaPanel.setCurrentDocPagePresenter(p)

            return self.mainAreaPanel



    def createNewDocPagePresenterTab(self):
        presenter = DocPagePresenter(self.mainAreaPanel, self)
        presenter.setLayerVisible(False)
        presenter.Hide()

        editor = self.createWindow({"name": "txteditor1",
                "presenter": presenter}, presenter)
        editor.setLayerVisible(False, "textedit")
        presenter.setSubControl("textedit", editor)

        htmlView = createWikiHtmlView(presenter, presenter, -1)
        htmlView.setLayerVisible(False, "preview")
        presenter.setSubControl("preview", htmlView)

#         mainsizer = LayerSizer()
#         mainsizer.Add(editor)
#         mainsizer.Add(htmlView)
#         presenter.SetSizer(mainsizer)

        return self.mainAreaPanel.appendDocPagePresenterTab(presenter)


    def appendLogMessage(self, msg):
        """
        Add message to log window, make log window visible if necessary
        """
        if self.configuration.getboolean("main", "log_window_autoshow"):
            self.windowLayouter.expandWindow("log")
        self.logWindow.appendMessage(msg)

    def hideLogWindow(self):
        self.windowLayouter.collapseWindow("log")


    def reloadMenuPlugins(self):
        if self.mainmenu is not None:
            self.menuFunctions = self.pluginManager.registerPluginAPI((
                    "MenuFunctions",1), ("describeMenuItems",))
                    
            self.loadExtensions()

#             self.pluginManager.loadPlugins( dirs, [ u'KeyBindings.py',
#                     u'EvalLibrary.py', u'WikiSyntax.py' ] )
            
            # This is a rebuild of an existing menu (after loading a new wikiData)
            clearMenu(self.pluginsMenu)
            self.fillPluginsMenu(self.pluginsMenu)
            
#             self.mainmenu.Replace(6, self.buildPluginsMenu(), "Pl&ugins")
            return



    def resourceSleep(self):
        """
        Free unnecessary resources if program is iconized
        """
        if self.sleepMode:
            return  # Already in sleep mode
        self.sleepMode = True
        
        toolBar = self.GetToolBar()
        if toolBar is not None:
            toolBar.Destroy()

        self.SetMenuBar(None)
        self.mainmenu.Destroy()

        # Set menu/menu items to None
        self.mainmenu = None
        self.recentWikisMenu = None
        self.textBlocksMenu = None
        self.favoriteWikisMenu = None
        # self.showOnTrayMenuItem = None

        # TODO Clear cache only if exactly one window uses centralized iconLookupCache
        #      Maybe weak references?
#         for k in self.iconLookupCache.keys():
#             self.iconLookupCache[k] = (self.iconLookupCache[k][0], None)
##      Even worse:  wxGetApp().getIconCache().clearIconBitmaps()

        gc.collect()


    def resourceWakeup(self):
        """
        Aquire resources after program is restored
        """
        if not self.sleepMode:
            return  # Already in wake mode
        self.sleepMode = False

        self.buildMainMenu()
        self.setShowToolbar(self.getConfig().getboolean("main", "toolbar_show",
                True))
        self.setShowOnTray()


    def testIt(self):
        self.reloadMenuPlugins()
        



#     def testIt(self):
#         self.hhelp = wx.html.HtmlHelpController()
#         self.hhelp.AddBook(join(self.wikiAppDir, "helptest/helptest.hhp"))
#         self.hhelp.DisplayID(1)

#     def testIt(self):
#         rect = self.statusBar.GetFieldRect(0)
#         
#         dc = wx.WindowDC(self.statusBar)
#         dc.SetBrush(wx.RED_BRUSH)
#         dc.SetPen(wx.RED_PEN)
#         dc.DrawRectangle(rect.x, rect.y, rect.width, rect.height)
#         dc.SetPen(wx.WHITE_PEN)
#         dc.SetFont(self.statusBar.GetFont())
#         dc.DrawText(u"Saving page", rect.x + 2, rect.y + 2)
#         dc.SetFont(wx.NullFont)
#         dc.SetBrush(wx.NullBrush)
#         dc.SetPen(wx.NullPen)

        # self.statusBar.Refresh()



    def OnIconize(self, evt):
        if self.lowResources:
            if self.IsIconized():
                self.resourceSleep()
            else:
                self.resourceWakeup()

        if self.configuration.getboolean("main", "showontray"):
            self.Show(not self.IsIconized())

        evt.Skip()


    def OnMaximize(self, evt):
        if self.lowResources:
            self.resourceWakeup()

        evt.Skip()


    # TODO Reset preview and other possible details
    def resetGui(self):
        # delete everything in the current tree
        self.tree.DeleteAllItems()
        
        viewsTree = self.windowLayouter.getWindowForName("viewstree")
        if viewsTree is not None:
            viewsTree.DeleteAllItems()

        # reset the editor
        self.getActiveEditor().loadWikiPage(None)
        self.getActiveEditor().SetSelection(-1, -1)
        self.getActiveEditor().EmptyUndoBuffer()
        self.getActiveEditor().Disable()

        # reset tray
        self.setShowOnTray()


    def _getRelativeWikiPath(self, path):
        """
        Converts the absolute path to a relative path if possible. Otherwise
        the unmodified path is returned.
        """
        relPath = relativeFilePath(self.wikiAppDir, path)
        
        if relPath is None:
            return path
        else:
            return relPath


    def _getStorableWikiPath(self, path):
        """
        Converts the absolute path to a relative path if possible and if option
        is set to do this. Otherwise the unmodified path is returned.
        """
        if not self.getConfig().getboolean("main", "wikiPathes_relative", False):
            return path

        return self._getRelativeWikiPath(path)


    def newWiki(self, wikiName, wikiDir):
        "creates a new wiki"
        wdhandlers = DbBackendUtils.listHandlers()
        if len(wdhandlers) == 0:
            self.displayErrorMessage(
                    _(u'No data handler available to create database.'))
            return

        wikiName = string.replace(wikiName, u" ", u"")
        wikiDir = join(wikiDir, wikiName)
        configFileLoc = join(wikiDir, u"%s.wiki" % wikiName)

#         self.statusBar.SetStatusText(uniToGui(u"Creating Wiki: %s" % wikiName), 0)

        createIt = True;
        if (exists(pathEnc(wikiDir))):
            dlg=wx.MessageDialog(self,
                    uniToGui(_(u"A wiki already exists in '%s', overwrite? "
                    u"(This deletes everything in and below this directory!)") %
                    wikiDir), _(u'Warning'), wx.YES_NO)
            result = dlg.ShowModal()
            dlg.Destroy()
            if result == wx.ID_YES:
                os.rmdir(wikiDir)  # TODO bug
                createIt = True
            elif result == wx.ID_NO:
                createIt = False

        if createIt:
            # Ask for the data handler to use
            index = wx.GetSingleChoiceIndex(_(u"Choose database type"),
                    _(u"Choose database type"), [wdh[1] for wdh in wdhandlers],
                    self)
            if index == -1:
                return

            wdhName = wdhandlers[index][0]
                
#             wikiDataFactory, createWikiDbFunc = DbBackendUtils.getHandler(self, 
#                     wdhName)
#                     
#             if wikiDataFactory is None:
#                 self.displayErrorMessage(
#                         'Data handler %s not available' % wdh[0])
#                 return
            

            # create the new dir for the wiki
            os.mkdir(wikiDir)

            allIsWell = True

            dataDir = join(wikiDir, "data")
            dataDir = mbcsDec(abspath(dataDir), "replace")[0]

            # create the data directory for the data files
            try:
                WikiDataManager.createWikiDb(self, wdhName, wikiName, dataDir,
                        False)
  #               createWikiDbFunc(wikiName, dataDir, False)
            except WikiDBExistsException:
                # The DB exists, should it be overwritten
                dlg=wx.MessageDialog(self, _(u'A wiki database already exists '
                        u'in this location, overwrite?'),
                        _(u'Wiki DB Exists'), wx.YES_NO)
                result = dlg.ShowModal()
                if result == wx.ID_YES:
  #                   createWikiDbFunc(wikiName, dataDir, True)
                    WikiDataManager.createWikiDb(self, wdhName, wikiName, dataDir,
                        True)
                else:
                    allIsWell = False

                dlg.Destroy()
            except Exception, e:
                self.displayErrorMessage(
                        _(u'There was an error creating the wiki database.'), e)
                traceback.print_exc()                
                allIsWell = False
            
            if (allIsWell):
                try:
                    self.hooks.newWiki(self, wikiName, wikiDir)
    
                    # everything is ok, write out the config file
                    # create a new config file for the new wiki
                    wikiConfig = wx.GetApp().createWikiConfiguration()
    #                 
                    wikiConfig.createEmptyConfig(configFileLoc)
                    wikiConfig.fillWithDefaults()
                    
                    wikiConfig.set("main", "wiki_name", wikiName)
                    wikiConfig.set("main", "last_wiki_word", wikiName)
                    wikiConfig.set("main", "wiki_database_type", wdhName)
                    wikiConfig.set("wiki_db", "data_dir", "data")
                    wikiConfig.save()

                    self.closeWiki()
                    
                    # open the new wiki
                    self.openWiki(configFileLoc)
                    p = self.wikiDataManager.createWikiPage(u"WikiSettings")
                    text = _(u"""++ Wiki Settings


These are your default global settings.

[global.importance.low.color: grey]
[global.importance.high.bold: true]
[global.contact.icon: contact]
[global.wrap: 70]

[icon: cog]
""")  # TODO Localize differently?
                    p.save(text, False)
                    p.update(text, False)
    
                    p = self.wikiDataManager.createWikiPage(u"ScratchPad")
                    text = u"++ Scratch Pad\n\n"
                    p.save(text, False)
                    p.update(text, False)
                    
                    self.getActiveEditor().GotoPos(self.getActiveEditor().GetLength())
                    self.getActiveEditor().AddText(u"\n\n\t* WikiSettings\n")
                    self.saveAllDocPages()
                    
                    # trigger hook
                    self.hooks.createdWiki(self, wikiName, wikiDir)
    
                    # reopen the root
                    self.openWikiPage(self.wikiName, False, False)

                except (IOError, OSError, DbAccessError), e:
                    self.lostAccess(e)
                    raise


    def _askForDbType(self):
        """
        Show dialog to ask for the wiki data handler (= database type)
        for opening a wiki
        """
        wdhandlers = DbBackendUtils.listHandlers()
        if len(wdhandlers) == 0:
            self.displayErrorMessage(
                    'No data handler available to open database.')
            return None

        # Ask for the data handler to use
        index = wx.GetSingleChoiceIndex(_(u"Choose database type"),
                _(u"Choose database type"), [wdh[1] for wdh in wdhandlers],
                self)
        if index == -1:
            return None
            
        return wdhandlers[index][0]



    def openWiki(self, wikiCombinedFilename, wikiWordsToOpen=None,
            ignoreWdhName=False, anchorToOpen=None):
        """
        opens up a wiki
        ignoreWdhName -- Should the name of the wiki data handler in the
                wiki config file (if any) be ignored?
        """

        # Fix special case
        if wikiWordsToOpen == (None,):
            wikiWordsToOpen = None

        # Save the state of the currently open wiki, if there was one open
        # if the new config is the same as the old, don't resave state since
        # this could be a wiki overwrite from newWiki. We don't want to overwrite
        # the new config with the old one.

        wikiCombinedFilename = abspath(join(self.wikiAppDir, wikiCombinedFilename))

        # make sure the config exists
        cfgPath, splittedWikiWord = WikiDataManager.splitConfigPathAndWord(
                wikiCombinedFilename)

        if cfgPath is None:
            self.displayErrorMessage(_(u"Invalid path or missing file '%s'")
                        % wikiCombinedFilename)

            # Try to remove combined filename from recent files if existing
            
            self.removeFromWikiHistory(wikiCombinedFilename)
#             try:
#                 self.wikiHistory.remove(
#                         self._getRelativeWikiPath(wikiCombinedFilename))
#                 self.informRecentWikisChanged()
#             except ValueError:
#                 pass


            return False

#        if self.wikiConfigFilename != wikiConfigFilename:
        self.closeWiki()
        
        # Remove path from recent file list if present (we will add it again
        # on top if everything went fine).
        
        self.removeFromWikiHistory(cfgPath)

        # trigger hooks
        self.hooks.openWiki(self, wikiCombinedFilename)

        if ignoreWdhName:
            # Explicitly ask for wiki data handler
            dbtype = self._askForDbType()
            if dbtype is None:
                return
        else:
            # Try to get handler name from wiki config file
            dbtype = None
#                     
        ignoreLock = self.getConfig().getboolean("main", "wikiLockFile_ignore",
                False)
        createLock = self.getConfig().getboolean("main", "wikiLockFile_create",
                True)

        while True:
            try:
                wikiDataManager = WikiDataManager.openWikiDocument(
                        cfgPath, self.wikiSyntax, dbtype, ignoreLock,
                        createLock)
                frmcode, frmtext = wikiDataManager.checkDatabaseFormat()
                if frmcode == 2:
                    # Unreadable db format
                    self.displayErrorMessage(
                            _(u"Error connecting to database in '%s'")
                            % cfgPath, frmtext)
                    return False
                elif frmcode == 1:
                    # Update needed -> ask
                    answer = wx.MessageBox(_(u"The wiki needs an update to work "
                            u"with this version of WikidPad. Older versions of "
                            u"WikidPad may be unable to read the wiki after "
                            u"an update."), _(u'Update database?'),
                            wx.OK | wx.CANCEL | wx.ICON_QUESTION, self)
                    if answer == wx.CANCEL:
                        return False

                wikiDataManager.connect()
                break
            except (UnknownDbHandlerException, DbHandlerNotAvailableException), e:
                # Could not get handler name from wiki config file
                # (probably old database) or required handler not available,
                # so ask user
                self.displayErrorMessage(unicode(e))
                dbtype = self._askForDbType()
                if dbtype is None:
                    return False
                    
                continue # Try again
            except LockedWikiException, e:
                # Database already in use by different instance
                answer = wx.MessageBox(_(u"Wiki '%s' is probably in use by different\n"
                        u"instance of WikidPad. Connect anyway (dangerous!)?") % cfgPath,
                        _(u"Wiki already in use"), wx.YES_NO, self)
                if answer == wx.NO:
                    return False
                else:
                    ignoreLock = True
                    continue # Try again

            except (BadConfigurationFileException,
                    MissingConfigurationFileException), e:
                self.displayErrorMessage(_(u"Configuration file '%s' is corrupted or "
                        u"missing.\nYou may have to change some settings in configuration "
                        u'page "Current Wiki" and below which were lost.') % cfgPath)
                wdhName = self._askForDbType()
                if wdhName is None:
                    return False

                wikiName = basename(cfgPath)[:-5] # Remove ".wiki"

                wikiConfig = wx.GetApp().createWikiConfiguration()

                wikiConfig.createEmptyConfig(cfgPath)
                wikiConfig.fillWithDefaults()

                wikiConfig.set("main", "wiki_name", wikiName)
                wikiConfig.set("main", "last_wiki_word", wikiName)
                wikiConfig.set("main", "wiki_database_type", wdhName)
                wikiConfig.set("wiki_db", "data_dir", "data")
                wikiConfig.save()
                
                continue # Try again

            except (IOError, OSError, DbReadAccessError,
                    BadConfigurationFileException,
                    MissingConfigurationFileException), e:
                # Something else went wrong
                self.displayErrorMessage(_(u"Error connecting to database in '%s'")
                        % cfgPath, e)
                if not isinstance(e, DbReadAccessError):
                    traceback.print_exc()
#                 self.lostAccess(e)
                return False
            except DbWriteAccessError, e:
                self.displayErrorMessage(_(u"Can't write to database '%s'")
                        % cfgPath, e)
                break   # ???

        # OK, things look good

        # set the member variables.

        self.wikiDataManager = wikiDataManager
        self.currentWikiDocumentProxyEvent.setWatchedEvent(
                self.wikiDataManager.getMiscEvent())

#         self.wikiDataManager.getMiscEvent().addListener(self)
        self.wikiData = wikiDataManager.getWikiData()

        self.wikiName = self.wikiDataManager.getWikiName()
        self.dataDir = self.wikiDataManager.getDataDir()
        
        self.getConfig().setWikiConfig(self.wikiDataManager.getWikiConfig())
        
        try:
            furtherWikiWords = []

            lastWikiWords = wikiWordsToOpen
            if wikiWordsToOpen is None:
                if splittedWikiWord:
                    # Take wiki word from combinedFilename
                    wikiWordsToOpen = (splittedWikiWord,)
                else:
                    # Try to find first wiki word
                    firstWikiWord = self.getConfig().get("main",
                        "first_wiki_word", u"")
                    if firstWikiWord != u"":
                        wikiWordsToOpen = (firstWikiWord,)
                    else:
                        # Nothing worked so take the last open wiki words
                        lastWikiWord = self.getConfig().get("main",
                                "last_wiki_word", u"")
                        fwws = self.getConfig().get("main",
                                "further_wiki_words", u"")
                        if fwws != u"":
                            furtherWikiWords = [unescapeForIni(w) for w in
                                    fwws.split(u";")]
                        else:
                            furtherWikiWords = ()
                        
                        wikiWordsToOpen = (lastWikiWord,) + \
                                tuple(furtherWikiWords)


            # reset the gui
#             self.resetGui()
            self.buildMainMenu()
    
            # enable the top level menus
            if self.mainmenu:
                self.mainmenu.EnableTop(1, 1)
                self.mainmenu.EnableTop(2, 1)
                self.mainmenu.EnableTop(3, 1)
                
            self.fireMiscEventKeys(("opened wiki",))

            # open the home page    # TODO!
            self.openWikiPage(self.wikiName)
            
            lastRoot = self.getConfig().get("main", "tree_last_root_wiki_word",
                    None)
            if not (lastRoot and
                    self.getWikiDocument().isDefinedWikiWord(lastRoot)):
                lastRoot = self.wikiName
            
            self.tree.setRootByWord(lastRoot)
            self.tree.readExpandedNodesFromConfig()
            self.tree.expandRoot()
            self.getConfig().set("main", "tree_last_root_wiki_word", lastRoot)

            viewsTree = self.windowLayouter.getWindowForName("viewstree")
            if viewsTree is not None:
                viewsTree.setViewsAsRoot()
                viewsTree.readExpandedNodesFromConfig()
                viewsTree.expandRoot()


            # Remove/Replace undefined wiki words
            wwo = []
            for word in wikiWordsToOpen:
                if self.getWikiDocument().isDefinedWikiWord(word):
                    wwo.append(word)
                    continue

                wordsStartingWith = self.getWikiData().getWikiWordsStartingWith(
                        word, True)
                if len(wordsStartingWith) > 0:
                    word = wordsStartingWith[0]
                    wwo.append(word)
                    continue

            wikiWordsToOpen = wwo




            # set status
    #         self.statusBar.SetStatusText(
    #                 uniToGui(u"Opened wiki '%s'" % self.wikiName), 0)
    
            # now try and open the last wiki page as leftmost tab
            if len(wikiWordsToOpen) > 0 and wikiWordsToOpen[0] != self.wikiName:
                firstWikiWord = wikiWordsToOpen[0]
                # if the word is not a wiki word see if a word that starts with the word can be found
                if not self.getWikiDocument().isDefinedWikiWord(firstWikiWord):
                    wordsStartingWith = self.getWikiData().getWikiWordsStartingWith(
                            firstWikiWord, True)
                    if wordsStartingWith:
                        firstWikiWord = wordsStartingWith[0]

                self.openWikiPage(firstWikiWord, anchor=anchorToOpen)
                self.findCurrentWordInTree()

            # If present, open further words in tabs on the right
            for word in wikiWordsToOpen[1:]:
                if not self.getWikiDocument().isDefinedWikiWord(word):
                    wordsStartingWith = self.getWikiData().getWikiWordsStartingWith(
                            word, True)
                    if wordsStartingWith:
                        word = wordsStartingWith[0]
                self.activatePageByUnifiedName(u"wikipage/" + word,
                        tabMode=3)
#                 self.activateWikiWord(word, tabMode=3)

            self.tree.SetScrollPos(wx.HORIZONTAL, 0)
    
            # enable the editor control whether or not the wiki root was found
            for dpp in self.getMainAreaPanel().getDocPagePresenters():
                e = dpp.getSubControl("textedit")
                e.Enable(True)

            # update the last accessed wiki config var
            self.lastAccessedWiki(self.getWikiConfigPath())

            # Rebuild text blocks menu
            self.rereadTextBlocks()
            
            self._refreshHotKeys()
            
            # reset tray
            self.setShowOnTray()

            # trigger hook
            self.hooks.openedWiki(self, self.wikiName, wikiCombinedFilename)
    
            # return that the wiki was opened successfully
            return True
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            return False


    def setCurrentWordAsRoot(self):
        """
        Set current wiki word as root of the tree
        """
        self.setWikiWordAsRoot(self.getCurrentWikiWord())


    def setHomeWordAsRoot(self):
        self.setWikiWordAsRoot(self.getWikiDocument().getWikiName())


    def setWikiWordAsRoot(self, word):
        if not self.requireReadAccess():
            return
        try:
            if word is not None and \
                    self.getWikiDocument().isDefinedWikiWord(word):
                self.tree.setRootByWord(word)
                self.tree.expandRoot()
                self.getConfig().set("main", "tree_last_root_wiki_word", word)

        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise


    def closeWiki(self, saveState=True):

        def errCloseAnywayMsg():
            return wx.MessageBox(_(u"There is no (write-)access to underlying wiki\n"
                    "Close anyway and loose possible changes?"),
                    _(u'Close anyway'),
                    wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self)


        if self.getWikiConfigPath():
            wd = self.getWikiDocument()
            # Do not require access here, otherwise the user will not be able to
            # close a disconnected wiki
            if not wd.getReadAccessFailed() and not wd.getWriteAccessFailed():
                try:
                    self.fireMiscEventKeys(("closing current wiki",))

                    if self.getWikiData() and saveState:
                        self.saveCurrentWikiState()
                except (IOError, OSError, DbAccessError), e:
                    self.lostAccess(e)
                    if errCloseAnywayMsg() == wx.NO:
                        raise
                    else:
                        traceback.print_exc()
                        self.fireMiscEventKeys(("dropping current wiki",))

                try:
                    self.lastAccessedWiki(self.getWikiConfigPath())
                    if self.getWikiData():
                        wd.release()
                except (IOError, OSError, DbAccessError), e:
                    pass                
                self.wikiData = None
                if self.wikiDataManager is not None:
                    self.currentWikiDocumentProxyEvent.setWatchedEvent(None)
                self.wikiDataManager = None
            else:
                # We had already a problem, so ask what to do
                if errCloseAnywayMsg() == wx.NO:
                    raise LossyWikiCloseDeniedException
                
                self.fireMiscEventKeys(("dropping current wiki",))

                self.wikiData = None
                if self.wikiDataManager is not None:
                    self.currentWikiDocumentProxyEvent.setWatchedEvent(None)
                self.wikiDataManager = None
                
            self._refreshHotKeys()

            self.getConfig().setWikiConfig(None)
            if self.clipboardInterceptor is not None:
                self.clipboardInterceptor.catchOff()

            self.resetGui()
            self.fireMiscEventKeys(("closed current wiki",))


    def saveCurrentWikiState(self):
        try:
            # write out the current config
            self.writeCurrentConfig()
    
            # save the current wiki page if it is dirty
            if self.isWikiLoaded():
                self.saveAllDocPages()
    
            # database commits
            if self.getWikiData():
                self.getWikiData().commit()
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise


    def requireReadAccess(self):
        """
        Check flag in WikiDocument if database is readable. If not, take
        measures to re-establish it. If read access is probably possible,
        return True
        """
        wd = self.getWikiDocument()
        if wd is None:
            wx.MessageBox(_(u"This operation requires an open database"),
                    _(u'No open database'), wx.OK, self)
            return False

        if not wd.getReadAccessFailed():
            return True

        while True:
            wd = self.getWikiDocument()
            if wd is None:
                return False

            self.SetFocus()
            result = wx.MessageBox(_(u"No connection to database. "
                    u"Try to reconnect?"), _(u'Reconnect database?'),
                    wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION, self)

            if result == wx.NO:
                return False

            self.statusBar.PushStatusText(
                    _(u"Trying to reconnect database..."), 0)
            try:
                try:
                    wd.reconnect()
                    wd.setNoAutoSaveFlag(False)
                    wd.setReadAccessFailed(False)
                    self.requireWriteAccess()  # Just to test it  # TODO ?
                    return True  # Success
                except DbReadAccessError, e:
                    sys.stderr.write(_(u"Error while trying to reconnect:\n"))
                    traceback.print_exc()
                    self.SetFocus()
                    self.displayErrorMessage(_(u'Error while reconnecting '
                            'database'), e)
            finally:
                self.statusBar.PopStatusText(0)


    def requireWriteAccess(self):
        """
        Check flag in WikiDocument if database is writable. If not, take
        measures to re-establish it. If write access is probably possible,
        return True
        """
        if not self.requireReadAccess():
            return False
        
        if not self.getWikiDocument().getWriteAccessFailed():
            return True

        while True:
            wd = self.getWikiDocument()
            if wd is None:
                return False

            self.SetFocus()
            result = wx.MessageBox(
                    _(u"This operation needs write access to database\n"
                    u"Try to write?"), _(u'Try writing?'),
                    wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION, self)

            if result == wx.NO:
                return False

            self.statusBar.PushStatusText(
                    _(u"Trying to write to database..."), 0)
            try:
                try:
                    # write out the current configuration
                    self.writeCurrentConfig()
                    self.getWikiData().testWrite()

                    wd.setNoAutoSaveFlag(False)
                    wd.setWriteAccessFailed(False)
                    return True  # Success
                except (IOError, OSError, DbWriteAccessError), e:
                    sys.stderr.write(_(u"Error while trying to write:\n"))
                    traceback.print_exc()
                    self.SetFocus()
                    self.displayErrorMessage(_(u'Error while writing to '
                            'database'), e)
            finally:
                self.statusBar.PopStatusText(0)


    def lostAccess(self, exc):
        if isinstance(exc, DbReadAccessError):
            self.lostReadAccess(exc)
        elif isinstance(exc, DbWriteAccessError):
            self.lostWriteAccess(exc)
        else:
            self.lostReadAccess(exc)


    def lostReadAccess(self, exc):
        """
        Called if read access was lost during an operation
        """
        if self.getWikiDocument().getReadAccessFailed():
            # Was already handled -> ignore
            return
            
        self.SetFocus()
        wx.MessageBox(_(u"Database connection error: %s.\n"
                u"Try to re-establish, then run \"Wiki\"->\"Reconnect\"") % unicode(exc),
                _(u'Connection lost'), wx.OK, self)

#         wd.setWriteAccessFailed(True) ?
        self.getWikiDocument().setReadAccessFailed(True)


    def lostWriteAccess(self, exc):
        """
        Called if read access was lost during an operation
        """
        if self.getWikiDocument().getWriteAccessFailed():
            # Was already handled -> ignore
            return

        self.SetFocus()
        wx.MessageBox(_(u"No write access to database: %s.\n"
                u" Try to re-establish, then run \"Wiki\"->\"Reconnect\"") % unicode(exc),
                _(u'Connection lost'), wx.OK, self)

        self.getWikiDocument().setWriteAccessFailed(True)


    def tryAutoReconnect(self):   # TODO ???
        """
        Try reconnect after an error, if not already tried automatically
        """
        wd = self.getWikiDocument()
        if wd is None:
            return False

        if wd.getAutoReconnectTriedFlag():
            # Automatic reconnect was tried already, so don't try again
            return False

        self.statusBar.PushStatusText(_(u"Trying to reconnect ..."), 0)

        try:
            try:
                wd.setNoAutoSaveFlag(True)
                wd.reconnect()
                wd.setNoAutoSaveFlag(False)
                return True
            except:
                sys.stderr.write(_(u"Error while trying to reconnect:") + u"\n")
                traceback.print_exc()
        finally:
            self.statusBar.PopStatusText(0)

        return False


    def openFuncPage(self, funcTag, **evtprops):
        dpp = self.getCurrentDocPagePresenter()
        if dpp is None:
            self.createNewDocPagePresenterTab()
            dpp = self.getCurrentDocPagePresenter()

        dpp.openFuncPage(funcTag, **evtprops)


    def openWikiPage(self, wikiWord, addToHistory=True,
            forceTreeSyncFromRoot=False, forceReopen=False, **evtprops):
        ## _prof.start()
        dpp = self.getCurrentDocPagePresenter()
        if dpp is None:
            self.createNewDocPagePresenterTab()
            dpp = self.getCurrentDocPagePresenter()

        dpp.openWikiPage(wikiWord, addToHistory, forceTreeSyncFromRoot,
                forceReopen, **evtprops)
        ## _prof.stop()


    def saveCurrentDocPage(self, force=False):
        dpp = self.getCurrentDocPagePresenter()
        if dpp is None:
            return
            
        dpp.saveCurrentDocPage(force)


    def activatePageByUnifiedName(self, unifName, tabMode=0):
        """
        tabMode -- 0:Same tab; 2: new tab in foreground; 3: new tab in background
        """
        # open the wiki page
        if tabMode & 2:
            # New tab
            presenter = self.createNewDocPagePresenterTab()
        else:
            # Same tab
            presenter = self.getCurrentDocPagePresenter()

        presenter.openDocPage(unifName, motionType="child")

        if not tabMode & 1:
            # Show in foreground
            self.getMainAreaPanel().showDocPagePresenter(presenter)

        return presenter


    def saveAllDocPages(self, force = False):
        if not self.requireWriteAccess():
            return

        try:
            self.fireMiscEventProps({"saving all pages": None, "force": force})
            self.refreshPageStatus()
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise


    def saveDocPage(self, page, text=None, pageAst=None):
        """
        Save page unconditionally
        """
        if page is None:
            return False

        if page.isReadOnlyEffect():
            return True   # return False?

        if not self.requireWriteAccess():
            return

        self.statusBar.PushStatusText(u"Saving page", 0)
        try:
            if text is None:
                # Try to retrieve text from editor
                editor = page.getTxtEditor()
                if editor is None:
                    # No editor -> nothing to do
                    return False

                text = page.getLiveText()
                pageAst = page.getLivePageAst()

            word = page.getWikiWord()
            if word is not None:
                # trigger hooks
                self.hooks.savingWikiWord(self, word)

            while True:
                try:
                    if word is not None:
                        # only for real wiki pages
                        page.save(self.getActiveEditor().cleanAutoGenAreas(text))
                        page.update(self.getActiveEditor().updateAutoGenAreas(text))   # ?
                        if pageAst is not None:
                            self.propertyChecker.checkPage(page, pageAst)

                        # trigger hooks
                        self.hooks.savedWikiWord(self, word)
                    else:
                        # for functional pages
                        page.save(text)
                        page.update(text)

                    self.getWikiData().commit()
                    return True
                except (IOError, OSError, DbAccessError), e:
                    self.lostAccess(e)
                    raise
        finally:
            self.statusBar.PopStatusText(0)


    def deleteWikiWord(self, wikiWord):
        if wikiWord and self.requireWriteAccess():
            try:
                if self.getWikiDocument().isDefinedWikiWord(wikiWord):
                    page = self.getWikiDocument().getWikiPage(wikiWord)
                    page.deletePage()
            except (IOError, OSError, DbAccessError), e:
                self.lostAccess(e)
                raise


    def renameWikiWord(self, wikiWord, toWikiWord, modifyText, **evtprops):
        """
        Renames current wiki word to toWikiWord.
        Returns True if renaming was done successful.
        
        modifyText -- Should the text of links to the renamed page be
                modified? (This text replacement works unreliably)
        """
        if wikiWord is None or not self.requireWriteAccess():
            return False

        try:
            self.saveAllDocPages()

            if wikiWord == self.getWikiDocument().getWikiName():
                # Renaming of root word = renaming of wiki config file
                wikiConfigFilename = self.getWikiDocument().getWikiConfigPath()
                self.removeFromWikiHistory(wikiConfigFilename)
#                 self.wikiHistory.remove(wikiConfigFilename)
                self.getWikiDocument().renameWikiWord(wikiWord, toWikiWord,
                        modifyText)
                # Store some additional information
                self.lastAccessedWiki(
                        self.getWikiDocument().getWikiConfigPath())
            else:
                self.getWikiDocument().renameWikiWord(wikiWord, toWikiWord,
                        modifyText)

            return True
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise
        except WikiDataException, e:
            traceback.print_exc()                
            self.displayErrorMessage(unicode(e))
            return False


    def findCurrentWordInTree(self):
        try:
            self.tree.buildTreeForWord(self.getCurrentWikiWord(), selectNode=True)
        except Exception, e:
            traceback.print_exc()


    def makeRelUrlAbsolute(self, relurl):
        """
        Return the absolute path for a rel: URL
        """
#         relpath = urllib.url2pathname(relurl[6:])
        relpath = pathnameFromUrl(relurl[6:], False)

        url = "file:" + urlFromPathname(
                abspath(join(dirname(self.getWikiConfigPath()), relpath)))

        return url


    def launchUrl(self, link):
#         link2 = flexibleUrlUnquote(link)
        if self.configuration.getint(
                "main", "new_window_on_follow_wiki_url") == 1 or \
                not link.startswith(u"wiki:"):

            if link.startswith(u"rel://"):
                # This is a relative link
                link = self.makeRelUrlAbsolute(link)

            try:
                OsAbstract.startFile(self, link)
            except Exception, e:
                traceback.print_exc()
                self.displayErrorMessage(_(u"Couldn't start file"), e)
                return False

            return True
        elif self.configuration.getint(
                "main", "new_window_on_follow_wiki_url") != 1:

            filePath, wikiWordToOpen, anchorToOpen = wikiUrlToPathWordAndAnchor(
                    link)
            if exists(filePath):
                self.openWiki(filePath, wikiWordsToOpen=(wikiWordToOpen,),
                        anchorToOpen=anchorToOpen)  # ?
                return True
            else:
                self.statusBar.SetStatusText(
                        uniToGui(_(u"Couldn't open wiki: %s") % link), 0)
                return False
        return False



    def refreshPageStatus(self, docPage = None):
        """
        Read information from page and present it in the field 1 of the
        status bar and in the title bar.
        """
        fmt = mbcsEnc(self.getConfig().get("main", "pagestatus_timeformat"),
                "replace")[0]

        if docPage is None:
            docPage = self.getCurrentDocPage()

        if docPage is None or not isinstance(docPage,
                (DocPages.WikiPage, DocPages.AliasWikiPage)):
            self.statusBar.SetStatusText(uniToGui(u""), 1)
            return

        pageStatus = u""   # wikiWord

        modTime, creaTime = docPage.getTimestamps()[:2]
        if modTime is not None:
#             pageStatus += _(u"Mod.: %s") % \
#                     mbcsDec(strftime(fmt, localtime(modTime)), "replace")[0]
#             pageStatus += _(u"; Crea.: %s") % \
#                     mbcsDec(strftime(fmt, localtime(creaTime)), "replace")[0]
            pageStatus += _(u"Mod.: %s") % strftimeUB(fmt, modTime)
            pageStatus += _(u"; Crea.: %s") % strftimeUB(fmt, creaTime)

        self.statusBar.SetStatusText(uniToGui(pageStatus), 1)

        self.SetTitle(uniToGui(u"%s: %s - %s - WikidPad" %
                (self.getWikiDocument().getWikiName(), docPage.getWikiWord(),
                self.getWikiConfigPath(), )))


    def viewWordSelection(self, title, words, motionType):
        """
        View a single choice to select a word to go to
        title -- Title of the dialog
        words -- Sequence of the words to choose from
        motionType -- motion type to set in openWikiPage if word was choosen
        """
        if not self.requireReadAccess():
            return
        try:
            dlg = ChooseWikiWordDialog(self, -1, words, motionType, title)
            dlg.CenterOnParent(wx.BOTH)
            dlg.ShowModal()
            dlg.Destroy()
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise


    def viewParents(self, ofWord):
        if not self.requireReadAccess():
            return
        try:
            parents = self.getWikiData().getParentRelationships(ofWord)
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise
        self.viewWordSelection(_(u"Parent nodes of '%s'") % ofWord, parents,
                "parent")


    def viewParentLess(self):
        if not self.requireReadAccess():
            return
        try:
            parentLess = self.getWikiData().getParentlessWikiWords()
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise
        self.viewWordSelection(_(u"Parentless nodes"), parentLess,
                "random")


    def viewChildren(self, ofWord):
        if not self.requireReadAccess():
            return
        try:
            children = self.getWikiData().getChildRelationships(ofWord)
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise
        self.viewWordSelection(_(u"Child nodes of '%s'") % ofWord, children,
                "child")


    def viewBookmarks(self):
        if not self.requireReadAccess():
            return
        try:
            bookmarked = self.getWikiData().getWordsWithPropertyValue(
                    "bookmarked", u"true")
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise
        self.viewWordSelection(_(u"Bookmarks"), bookmarked,
                "random")


    def removeFromWikiHistory(self, path):
        """
        Remove path from wiki history (if present) and sends an event.
        """
        try:
            self.wikiHistory.remove(self._getRelativeWikiPath(path))
            self.informRecentWikisChanged()
        except ValueError:
            pass

        # Try absolute
        try:
            self.wikiHistory.remove(path)
            self.informRecentWikisChanged()
        except ValueError:
            pass


    def lastAccessedWiki(self, wikiConfigFilename):
        """
        Writes to the global config the location of the last accessed wiki
        and updates file history.
        """
        wikiConfigFilename = self._getStorableWikiPath(wikiConfigFilename)
        
        # create a new config file for the new wiki
        self.configuration.set("main", "last_wiki", wikiConfigFilename)
        if wikiConfigFilename not in self.wikiHistory:
            self.wikiHistory = [wikiConfigFilename] + self.wikiHistory

            # only keep most recent items
            maxLen = self.configuration.getint(
                    "main", "recentWikisList_length", 5)
            if len(self.wikiHistory) > maxLen:
                self.wikiHistory = self.wikiHistory[:maxLen]

            self.informRecentWikisChanged()

        self.configuration.set("main", "last_active_dir", dirname(wikiConfigFilename))
        self.writeGlobalConfig()


    # Only needed for scripts
    def setAutoSave(self, onOrOff):
        self.autoSave = onOrOff
        self.configuration.set("main", "auto_save", self.autoSave)


    def setShowTreeControl(self, onOrOff):
        self.windowLayouter.expandWindow("maintree", onOrOff)
        if onOrOff:
            self.windowLayouter.focusWindow("maintree")


    def getShowToolbar(self):
        return not self.GetToolBar() is None

    def setShowToolbar(self, onOrOff):
        """
        Control, if toolbar should be shown or not
        """
        self.getConfig().set("main", "toolbar_show", bool(onOrOff))

        if bool(onOrOff) == self.getShowToolbar():
            # Desired state already reached
            return

        if onOrOff:
            self.buildToolbar()
        else:
            self.fastSearchField = None    
            self.GetToolBar().Destroy()
            self.SetToolBar(None)


    def setShowDocStructure(self, onOrOff):
        self.windowLayouter.expandWindow("doc structure", onOrOff)
        if onOrOff:
            self.windowLayouter.focusWindow("doc structure")

    def setShowTimeView(self, onOrOff):
        self.windowLayouter.expandWindow("time view", onOrOff)
        if onOrOff:
            self.windowLayouter.focusWindow("time view")


    def getStayOnTop(self):
        """
        Returns if this window is set to stay on top of all others
        """
        return bool(self.GetWindowStyleFlag() & wx.STAY_ON_TOP)

    def setStayOnTop(self, onOrOff):
        style = self.GetWindowStyleFlag()
        
        if onOrOff:
            style |= wx.STAY_ON_TOP
        else:
            style &= ~wx.STAY_ON_TOP

        self.SetWindowStyleFlag(style)


    def setShowOnTray(self, onOrOff=None):
        """
        Update UI and config according to the settings of onOrOff.
        If onOrOff is omitted, UI is updated according to current
        setting of the global config
        """
        if not onOrOff is None:
            self.configuration.set("main", "showontray", onOrOff)
        else:
            onOrOff = self.configuration.getboolean("main", "showontray")


        tooltip = None
        if self.getWikiConfigPath():  # If a wiki is open
            tooltip = _(u"Wiki: %s") % self.getWikiConfigPath()  # self.wikiName
            iconName = self.getConfig().get("main", "wiki_icon", u"")
        else:
            tooltip = u"Wikidpad"
            iconName = u""

        bmp = None
        if iconName != u"":
            bmp = wx.GetApp().getIconCache().lookupIcon(iconName)


        if onOrOff:
            if self.tbIcon is None:
                self.tbIcon = TaskBarIcon(self)

            if Configuration.isLinux():
                # On Linux, the tray icon must be resized here, otherwise
                # it might be too large.
                if bmp is not None:
                    img = bmp.ConvertToImage()
                else:
                    img = wx.Image(os.path.join(self.wikiAppDir, 'icons',
                            'pwiki.ico'), wx.BITMAP_TYPE_ICO)

                img.Rescale(20, 20)
                bmp = wx.BitmapFromImage(img)
                icon = wx.IconFromBitmap(bmp)
                self.tbIcon.SetIcon(icon, uniToGui(tooltip))
            else:
                if bmp is not None:                
                    self.tbIcon.SetIcon(wx.IconFromBitmap(bmp),
                            uniToGui(tooltip))
                else:
                    self.tbIcon.SetIcon(wx.GetApp().standardIcon,
                            uniToGui(tooltip))

        else:
            if self.tbIcon is not None:
                if self.tbIcon.IsIconInstalled():
                    self.tbIcon.RemoveIcon()

                self.tbIcon.Destroy()
                self.tbIcon = None

#         # TODO  Move to better function
#         if bmp is not None:                
#             self.SetIcon(wx.IconFromBitmap(bmp))
#         else:
#             print "setShowOnTray25", repr(os.path.join(self.wikiAppDir,
#                     'icons', 'pwiki.ico')), repr(wx.Icon(os.path.join(self.wikiAppDir,
#                     'icons', 'pwiki.ico'), wx.BITMAP_TYPE_ICO))
# #             self.SetIcon(wx.Icon(os.path.join(self.wikiAppDir,
# #                     'icons', 'pwiki.ico'), wx.BITMAP_TYPE_ICO))
#             self.SetIcon(wx.GetApp().standardIcon)


    def setHideUndefined(self, onOrOff=None):
        """
        Set if undefined WikiWords should be hidden in the tree
        """

        if not onOrOff is None:
            self.configuration.set("main", "hideundefined", onOrOff)
        else:
            onOrOff = self.configuration.getboolean("main", "hideundefined")


#     _LAYOUT_WITHOUT_VIEWSTREE = "name:main area panel;"\
#         "layout relation:%s&layout relative to:main area panel&name:maintree&"\
#             "layout sash position:170&layout sash effective position:170;"\
#         "layout relation:below&layout relative to:main area panel&name:log&"\
#             "layout sash position:1&layout sash effective position:120"
# 
#     _LAYOUT_WITH_VIEWSTREE = "name:main area panel;"\
#             "layout relation:%s&layout relative to:main area panel&name:maintree&"\
#                 "layout sash position:170&layout sash effective position:170;"\
#             "layout relation:%s&layout relative to:maintree&name:viewstree;"\
#             "layout relation:below&layout relative to:main area panel&name:log&"\
#                 "layout sash position:1&layout sash effective position:120"

    def changeLayoutByCf(self, layoutCfStr):
        """
        Create a new window layouter according to the
        layout configuration string layoutCfStr. Try to reuse and reparent
        existing windows.
        BUG: Reparenting seems to disturb event handling for tree events and
            isn't available for all OS'
        """
#         # Reparent reusable windows so they aren't destroyed when
#         #   cleaning main window
#         # TODO Reparent not available for all OS'
#         cachedWindows = {}
# #         for n, w in self.windowLayouter.winNameToObject.iteritems():
#         for n, w in self.windowLayouter.winNameToProxy.iteritems():
#             print "--toCache", repr(n), repr(w)
#             cachedWindows[n] = w
# #             w.Reparent(None)
#             w.Reparent(self)
# 
#         self.windowLayouter.cleanMainWindow(cachedWindows.values())
# 
#         # make own creator function which provides already existing windows
#         def cachedCreateWindow(winProps, parent):
#             """
#             Wrapper around _actualCreateWindow to maintain a cache
#             of already existing windows
#             """
#             winName = winProps["name"]
# 
#             # Try in cache:
#             window = cachedWindows.get(winName)
# #             print "--cachedCreateWindow", repr(winName), repr(window)
#             if window is not None:
#                 window.Reparent(parent)    # TODO Reparent not available for all OS'
#                 del cachedWindows[winName]
#                 return window
# 
#             window = self.createWindow(winProps, parent)
# 
#             return window
#         
#         self.windowLayouter = WindowSashLayouter(self, cachedCreateWindow)
# 
#         # Destroy windows which weren't reused
#         # TODO Call close method of object window if present
#         for n, w in cachedWindows.iteritems():
#             w.Destroy()
# 
#         self.windowLayouter.setWinPropsByConfig(layoutCfStr)

        # Handle no size events while realizing layout
        self.Unbind(wx.EVT_SIZE)

        self.windowLayouter.realizeNewLayoutByCf(layoutCfStr)

#         self.windowLayouter.realize()
        self.windowLayouter.layout()

        wx.EVT_SIZE(self, self.OnSize)

        self.tree = self.windowLayouter.getWindowForName("maintree")
        self.logWindow = self.windowLayouter.getWindowForName("log")


#     def getClipboardCatcher(self):
#         return self.clipboardCatcher is not None and \
#                 self.clipboardCatcher.isActive()

    def OnClipboardCatcherOff(self, evt):
        self.clipboardInterceptor.catchOff()

    def OnClipboardCatcherAtPage(self, evt):
        if self.isReadOnlyPage():
            return

        self.clipboardInterceptor.catchAtPage(self.getCurrentDocPage())

    def OnClipboardCatcherAtCursor(self, evt):
        if self.isReadOnlyPage():
            return

        self.clipboardInterceptor.catchAtCursor()


    def OnUpdateClipboardCatcher(self, evt):
        cc = self.clipboardInterceptor
        if cc is None:
            return  # Shouldn't be called anyway
            
        wikiDoc = self.getWikiDocument()
        enableCatcher = not self.isReadOnlyPage()

        if evt.GetId() == GUI_ID.CMD_CLIPBOARD_CATCHER_OFF:
            evt.Check(cc.getMode() == cc.MODE_OFF)
        elif evt.GetId() == GUI_ID.CMD_CLIPBOARD_CATCHER_AT_CURSOR:
            evt.Enable(enableCatcher)
            evt.Check(cc.getMode() == cc.MODE_AT_CURSOR)
        elif evt.GetId() == GUI_ID.CMD_CLIPBOARD_CATCHER_AT_PAGE:
            evt.Enable(enableCatcher)
            if cc.getMode() == cc.MODE_AT_PAGE:
                evt.Check(True)
                evt.SetText(_(u"Clipboard Catcher at: %s\t%s") % 
                        (self.clipboardInterceptor.getWikiWord(),
                        self.keyBindings.CatchClipboardAtPage))
            else:
                evt.Check(False)
                evt.SetText(_(u'Clipboard Catcher at Page') + u'\t' +
                        self.keyBindings.CatchClipboardAtPage)

    def writeGlobalConfig(self):
        "writes out the global config file"
        try:
            self.configuration.save()
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise
        except Exception, e:
            self.displayErrorMessage(_(u"Error saving global configuration"), e)


    def writeCurrentConfig(self):
        "writes out the current config file"
        try:
            self.configuration.save()
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise
        except Exception, e:
            self.displayErrorMessage(_(u"Error saving current configuration"), e)


    def showWikiWordOpenDialog(self):
        dlg = OpenWikiWordDialog(self, -1)
        try:
            dlg.CenterOnParent(wx.BOTH)
            dlg.ShowModal()
#             if dlg.ShowModal() == wxID_OK:
#                 wikiWord = dlg.GetValue()
#                 if wikiWord:
#                     self.openWikiPage(wikiWord, forceTreeSyncFromRoot=True)
            self.getActiveEditor().SetFocus()
        finally:
            dlg.Destroy()


    def showWikiWordRenameDialog(self, wikiWord=None):
        if wikiWord is None:
            wikiWord = self.getCurrentWikiWord()

        if wikiWord is None:
            self.displayErrorMessage(_(u"No real wiki word selected to rename"))
            return
        
        if self.isReadOnlyPage():
            return

        wikiWord = self.getWikiData().getAliasesWikiWord(wikiWord)
        dlg = wx.TextEntryDialog(self, uniToGui(_(u"Rename '%s' to:") %
                wikiWord), _(u"Rename Wiki Word"), wikiWord, wx.OK | wx.CANCEL)

        try:
            while dlg.ShowModal() == wx.ID_OK and \
                    not self.showWikiWordRenameConfirmDialog(wikiWord,
                            guiToUni(dlg.GetValue())):
                pass

        finally:
            dlg.Destroy()

    # TODO Unicode
    def showStoreVersionDialog(self):
        dlg = wx.TextEntryDialog (self, _(u"Description:"),
                                 _(u"Store new version"), u"",
                                 wx.OK | wx.CANCEL)

        description = None
        if dlg.ShowModal() == wx.ID_OK:
            description = dlg.GetValue()
        dlg.Destroy()

        if not description is None:
            self.saveAllDocPages()
            self.getWikiData().storeVersion(description)


    def showDeleteAllVersionsDialog(self):
        result = wx.MessageBox(_(u"Do you want to delete all stored versions?"),
                _(u"Delete All Versions"),
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self)

        if result == wx.YES:
            self.getWikiData().deleteVersioningData()


    def showSavedVersionsDialog(self):
        if not self.getWikiData().hasVersioningData():
            dlg=wx.MessageDialog(self,
                    _(u"This wiki does not contain any version information"),
                    _(u'Retrieve version'), wx.OK)
            dlg.ShowModal()
            dlg.Destroy()
            return

        dlg = SavedVersionsDialog(self, -1)
        dlg.CenterOnParent(wx.BOTH)

        version = None
        if dlg.ShowModal() == wx.ID_OK:
            version = dlg.GetValue()
        dlg.Destroy()

        if version:
            dlg=wx.MessageDialog(self,
                    _(u"This will overwrite current content if not stored as "
                    u"version. Continue?"),
                    _(u'Retrieve version'), wx.YES_NO)
            if dlg.ShowModal() == wx.ID_YES:
                dlg.Destroy()
                self.saveAllDocPages()
                word = self.getCurrentWikiWord()
                self.getWikiData().applyStoredVersion(version[0])
                self.rebuildWiki(skipConfirm=True)
                ## self.tree.collapse()
                self.openWikiPage(self.getCurrentWikiWord(), forceTreeSyncFromRoot=True, forceReopen=True)
                ## self.findCurrentWordInTree()
            else:
                dlg.Destroy()


    # TODO Check if new name already exists (?)
    def showWikiWordRenameConfirmDialog(self, wikiWord, toWikiWord):
        """
        Checks if renaming operation is valid, presents either an error
        message or a confirmation dialog.
        Returns -- True iff renaming was done successfully
        """
#         wikiWord = self.getCurrentWikiWord()

        if not toWikiWord or len(toWikiWord) == 0:
            return False

        if not self.getFormatting().isNakedWikiWord(toWikiWord):
            self.displayErrorMessage(_(u"'%s' is an invalid wiki word") % toWikiWord)
            return False

        if wikiWord == toWikiWord:
            self.displayErrorMessage(_(u"Can't rename to itself"))
            return False

        if wikiWord == "ScratchPad":
            self.displayErrorMessage(_(u"The scratch pad cannot be renamed."))
            return False

        try:
            if not self.getWikiDocument().isCreatableWikiWord(toWikiWord):
                self.displayErrorMessage(
                        _(u"Cannot rename to '%s', word already exists") %
                        toWikiWord)
                return False

            # Link rename mode from options
            lrm = self.getConfig().getint("main",
                    "wikiWord_rename_wikiLinks", 2)
            if lrm == 0:
                result = wx.NO
            elif lrm == 1:
                result = wx.YES
            else: # lrm == 2: ask for each rename operation
                result = wx.MessageBox(
                        _(u"Do you want to modify all links to the wiki word "
                        u"'%s' renamed to '%s' (this operation is unreliable)?") %
                        (wikiWord, toWikiWord),
                        _(u'Rename Wiki Word'),
                        wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self)

            if result == wx.YES or result == wx.NO:
                try:
                    self.renameWikiWord(wikiWord, toWikiWord, result == wx.YES)
                    return True
                except WikiDataException, e:
                    traceback.print_exc()                
                    self.displayErrorMessage(unicode(e))
    
            return False
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise


    def showSearchDialog(self):
        if self.findDlg != None:
            if isinstance(self.findDlg, SearchWikiDialog):
                self.findDlg.SetFocus()
            return

        self.findDlg = SearchWikiDialog(self, -1)
        self.findDlg.CenterOnParent(wx.BOTH)
        self.findDlg.Show()


    def showWikiWordDeleteDialog(self, wikiWord=None):
        if wikiWord is None:
            wikiWord = self.getCurrentWikiWord()

        if wikiWord == u"ScratchPad":
            self.displayErrorMessage(_(u"The scratch pad cannot be deleted"))
            return

        if wikiWord is None:
            self.displayErrorMessage(_(u"No real wiki word to delete"))
            return
            
        if self.isReadOnlyPage():
            return

        wikiWord = self.getWikiData().getAliasesWikiWord(wikiWord)
        dlg=wx.MessageDialog(self,
                uniToGui(_(u"Are you sure you want to delete wiki word '%s'?") % wikiWord),
                _(u'Delete Wiki Word'), wx.YES_NO | wx.NO_DEFAULT)
        result = dlg.ShowModal()
        if result == wx.ID_YES:
            try:
                self.saveAllDocPages()
                self.deleteWikiWord(wikiWord)
            except (IOError, OSError, DbAccessError), e:
                self.lostAccess(e)
                raise
            except WikiDataException, e:
                self.displayErrorMessage(unicode(e))

        dlg.Destroy()


    def showFindReplaceDialog(self):
        if self.findDlg != None:
            if isinstance(self.findDlg, SearchPageDialog):
                self.findDlg.SetFocus()
            return

        self.findDlg = SearchPageDialog(self, -1)
        self.findDlg.CenterOnParent(wx.BOTH)
        self.findDlg.Show()


    def showReplaceTextByWikiwordDialog(self):
        if self.getCurrentWikiWord() is None:
            self.displayErrorMessage(_(u"No real wiki word to modify"))
            return
        
        if self.isReadOnlyPage():
            return

        wikiWord = ""
        newWord = True
        try:
            while True:
                wikiWord = guiToUni(wx.GetTextFromUser(
                        _(u"Replace text by WikiWord:"),
                        _(u"Replace by Wiki Word"), wikiWord, self))
                        
                if not wikiWord:
                    return False

                formatting = self.getFormatting()
                wikiWord = formatting.wikiWordToLabel(wikiWord)
                if not formatting.isNakedWikiWord(wikiWord):
                    self.displayErrorMessage(_(u"'%s' is an invalid wiki word") % wikiWord)
                    continue
#                     return False

                if not self.getWikiDocument().isCreatableWikiWord(wikiWord):
                    result = wx.MessageBox(uniToGui(_(
                            u'Wiki word %s exists already\n'
                            u'Would you like to append to the word?') %
                            wikiWord), _(u'Word exists'),
                            wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION, self)
                    
                    if result == wx.NO:
                        continue
                    
                    wikiWord = self.getWikiDocument().getAliasesWikiWord(wikiWord)
                    newWord = False

                break

#                 self.displayErrorMessage(u"'%s' exists already" % wikiWord)
#                         # TODO Allow retry or append/replace
#                 return False

            text = self.getActiveEditor().GetSelectedText()
            if newWord:
                page = self.wikiDataManager.createWikiPage(wikiWord)
                # TODO Respect template property?
                title = self.wikiDataManager.getWikiPageTitle(wikiWord)
                if title is not None:
                    ptp = self.getFormatting().getPageTitlePrefix()
                    self.saveDocPage(page, u"%s %s\n\n%s" % (ptp, title, text),
                            None)
                else:
                    self.saveDocPage(page, text, None)
            else:
                page = self.wikiDataManager.getWikiPage(wikiWord)
                page.appendLiveText(u"\n\n" + text)

            self.getActiveEditor().ReplaceSelection(
                    self.getFormatting().normalizeWikiWord(wikiWord))
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise


    def showSelectIconDialog(self):
#         dlg = SelectIconDialog(self, -1, wx.GetApp().getIconCache())
#         dlg.CenterOnParent(wx.BOTH)
#         if dlg.ShowModal() == wx.ID_OK:
#             iconname = dlg.GetValue()
# 
#         dlg.Destroy()
# 
        iconname = SelectIconDialog.runModal(self, -1,
                wx.GetApp().getIconCache())

        if iconname:
            self.insertAttribute("icon", iconname)

    def showDateformatDialog(self):
        fmt = self.configuration.get("main", "strftime")

        dlg = DateformatDialog(self, -1, self, deffmt = fmt)
        dlg.CenterOnParent(wx.BOTH)
        dateformat = None

        if dlg.ShowModal() == wx.ID_OK:
            dateformat = dlg.GetValue()
        dlg.Destroy()

        if not dateformat is None:
            self.configuration.set("main", "strftime", dateformat)

    def showOptionsDialog(self):
        dlg = OptionsDialog(self, -1)
        dlg.CenterOnParent(wx.BOTH)

        result = dlg.ShowModal()
        oldSettings = dlg.getOldSettings()
        
        dlg.Destroy()

        if result == wx.ID_OK:
            # Perform operations to reset GUI parts after option changes
            self.autoSaveDelayAfterKeyPressed = self.configuration.getint(
                    "main", "auto_save_delay_key_pressed")
            self.autoSaveDelayAfterDirty = self.configuration.getint(
                    "main", "auto_save_delay_dirty")
            maxLen = self.configuration.getint(
                    "main", "recentWikisList_length", 5)
            self.wikiHistory = self.wikiHistory[:maxLen]

            self.setShowOnTray()
            self.setHideUndefined()
            self.rereadRecentWikis()
            self.refreshPageStatus()
            
            # TODO Move this to WikiDataManager!
            # Set file storage according to configuration
            fs = self.getWikiDataManager().getFileStorage()
            
            fs.setModDateMustMatch(self.configuration.getboolean("main",
                    "fileStorage_identity_modDateMustMatch", False))
            fs.setFilenameMustMatch(self.configuration.getboolean("main",
                    "fileStorage_identity_filenameMustMatch", False))
            fs.setModDateIsEnough(self.configuration.getboolean("main",
                    "fileStorage_identity_modDateIsEnough", False))


            # Build new layout config string
            newLayoutMainTreePosition = self.configuration.getint("main",
                "mainTree_position", 0)
            newLayoutViewsTreePosition = self.configuration.getint("main",
                "viewsTree_position", 0)
            newLayoutDocStructurePosition = self.configuration.getint("main",
                "docStructure_position", 0)
            newLayoutTimeViewPosition = self.configuration.getint("main",
                "timeView_position", 0)    
            if self.layoutViewsTreePosition != newLayoutViewsTreePosition or \
                    self.layoutMainTreePosition != newLayoutMainTreePosition or \
                    self.layoutDocStructurePosition != newLayoutDocStructurePosition or \
                    self.layoutTimeViewPosition != newLayoutTimeViewPosition:

                self.layoutViewsTreePosition = newLayoutViewsTreePosition
                self.layoutMainTreePosition = newLayoutMainTreePosition
                self.layoutDocStructurePosition = newLayoutDocStructurePosition
                self.layoutTimeViewPosition = newLayoutTimeViewPosition

                mainPos = {0:"left", 1:"right", 2:"above", 3:"below"}\
                        [newLayoutMainTreePosition]

                # Set layout for main tree
                layoutCfStr = "name:main area panel;"\
                        "layout relation:%s&layout relative to:main area panel&name:maintree&"\
                        "layout sash position:170&layout sash effective position:170" % \
                        mainPos

                # Add layout for Views tree
                if newLayoutViewsTreePosition > 0:
#                     # Don't show "Views" tree
#                     layoutCfStr = self._LAYOUT_WITHOUT_VIEWSTREE % mainPos
#                 else:
                    viewsPos = {1:"above", 2:"below", 3:"left", 4:"right"}\
                            [newLayoutViewsTreePosition]
#                     layoutCfStr += self._LAYOUT_WITH_VIEWSTREE % \
#                             (mainPos, viewsPos)
                    layoutCfStr += ";layout relation:%s&layout relative to:maintree&name:viewstree" % \
                            viewsPos

                if newLayoutTimeViewPosition > 0:
                    timeViewPos = {1:"left", 2:"right", 3:"above", 4:"below"}\
                        [newLayoutTimeViewPosition]
                    layoutCfStr += ";layout relation:%s&layout relative to:main area panel&name:time view&"\
                                "layout sash position:120&layout sash effective position:120" % \
                                timeViewPos

                # Layout for doc structure window
                if newLayoutDocStructurePosition > 0:
                    docStructPos = {1:"left", 2:"right", 3:"above", 4:"below"}\
                        [newLayoutDocStructurePosition]
                    layoutCfStr += ";layout relation:%s&layout relative to:main area panel&name:doc structure&"\
                                "layout sash position:120&layout sash effective position:120" % \
                                docStructPos

                # Layout for log window
                layoutCfStr += ";layout relation:below&layout relative to:main area panel&name:log&"\
                            "layout sash position:1&layout sash effective position:120"
                            

                self.configuration.set("main", "windowLayout", layoutCfStr)
                # Call of changeLayoutByCf() crashes on Linux/GTK so save
                # data beforehand
                self.saveCurrentWikiState()
                self.changeLayoutByCf(layoutCfStr)
            
            self.userActionCoord.applyConfiguration()
            self._refreshHotKeys()

            wx.GetApp().fireMiscEventProps({"options changed": True,
                    "old config settings": oldSettings})


    def OnCmdExportDialog(self, evt):
        self.saveAllDocPages()
        self.getWikiData().commit()

        dlg = ExportDialog(self, -1)
        dlg.CenterOnParent(wx.BOTH)

        result = dlg.ShowModal()
        dlg.Destroy()


    EXPORT_PARAMS = {
            GUI_ID.MENU_EXPORT_WHOLE_AS_PAGE:
                    (Exporters.HtmlXmlExporter, u"html_single", None),
            GUI_ID.MENU_EXPORT_WHOLE_AS_PAGES:
                    (Exporters.HtmlXmlExporter, u"html_multi", None),
            GUI_ID.MENU_EXPORT_WORD_AS_PAGE:
                    (Exporters.HtmlXmlExporter, u"html_single", None),
            GUI_ID.MENU_EXPORT_SUB_AS_PAGE:
                    (Exporters.HtmlXmlExporter, u"html_single", None),
            GUI_ID.MENU_EXPORT_SUB_AS_PAGES:
                    (Exporters.HtmlXmlExporter, u"html_multi", None),
            GUI_ID.MENU_EXPORT_WHOLE_AS_XML:
                    (Exporters.HtmlXmlExporter, u"xml", None),
            GUI_ID.MENU_EXPORT_WHOLE_AS_RAW:
                    (Exporters.TextExporter, u"raw_files", (1,))
            }


    def OnExportWiki(self, evt):
        import SearchAndReplace as Sar

        defdir = self.getConfig().get("main", "export_default_dir", u"")
        if defdir == u"":
            defdir = self.getLastActiveDir()
        
        typ = evt.GetId()
        if typ != GUI_ID.MENU_EXPORT_WHOLE_AS_XML:
            # Export to dir
            dest = wx.DirSelector(_(u"Select Export Directory"), defdir,
            wx.DD_DEFAULT_STYLE|wx.DD_NEW_DIR_BUTTON, parent=self)
        else:
            # Export to file
            dest = wx.FileSelector(_(u"Select Export File"),
                    defdir,
                    default_filename = "", default_extension = "",
                    wildcard = _(u"XML files (*.xml)|*.xml|All files (*.*)|*"),
                    flags=wx.SAVE | wx.OVERWRITE_PROMPT, parent=self)
        
        try:
            if dest:
                if typ in (GUI_ID.MENU_EXPORT_WHOLE_AS_PAGE,
                        GUI_ID.MENU_EXPORT_WHOLE_AS_PAGES,
                        GUI_ID.MENU_EXPORT_WHOLE_AS_XML,
                        GUI_ID.MENU_EXPORT_WHOLE_AS_RAW):
                    # Export whole wiki
    
                    lpOp = Sar.ListWikiPagesOperation()
                    item = Sar.AllWikiPagesNode(lpOp)
                    lpOp.setSearchOpTree(item)
                    lpOp.ordering = "asroottree"  # Slow, but more intuitive
                    wordList = self.getWikiDocument().searchWiki(lpOp)
    
    #                 wordList = self.getWikiData().getAllDefinedWikiPageNames()
                    
                elif typ in (GUI_ID.MENU_EXPORT_SUB_AS_PAGE,
                        GUI_ID.MENU_EXPORT_SUB_AS_PAGES):
                    # Export a subtree of current word
                    if self.getCurrentWikiWord() is None:
                        self.displayErrorMessage(
                                _(u"No real wiki word selected as root"))
                        return
                    lpOp = Sar.ListWikiPagesOperation()
                    item = Sar.ListItemWithSubtreeWikiPagesNode(lpOp,
                            [self.getCurrentWikiWord()], -1)
                    lpOp.setSearchOpTree(item)
                    lpOp.ordering = "asroottree"  # Slow, but more intuitive
                    wordList = self.getWikiDocument().searchWiki(lpOp)
    
    #                 wordList = self.getWikiData().getAllSubWords(
    #                         [self.getCurrentWikiWord()])
                else:
                    if self.getCurrentWikiWord() is None:
                        self.displayErrorMessage(
                                _(u"No real wiki word selected as root"))
                        return

                    wordList = (self.getCurrentWikiWord(),)

                expclass, exptype, addopt = self.EXPORT_PARAMS[typ]
                
                self.saveAllDocPages()
                self.getWikiData().commit()

               
                ob = expclass(self)
                if addopt is None:
                    # Additional options not given -> take default provided by exporter
                    addopt = ob.getAddOpt(None)

                try:
                    ob.export(self.getWikiDataManager(), wordList, exptype, dest,
                            False, addopt)
                except ExportException, e:
                    self.displayErrorMessage(_(u"Error on export"), e)
    
                self.configuration.set("main", "last_active_dir", dest)

        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise


    def OnCmdImportDialog(self, evt):
        if self.isReadOnlyWiki():
            return

        self.saveAllDocPages()
        self.getWikiData().commit()

        dlg = ImportDialog(self, -1, self)
        dlg.CenterOnParent(wx.BOTH)

        result = dlg.ShowModal()
        dlg.Destroy()


    def showAddFileUrlDialog(self):
        if self.isReadOnlyPage():
            return

        dlg = wx.FileDialog(self, _(u"Choose a file to create URL for"),
                self.getLastActiveDir(), "", "*.*", wx.OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            url = urlFromPathname(dlg.GetPath())
            if dlg.GetPath().endswith(".wiki"):
                url = "wiki:" + url
            else:
#                 doCopy = False  # Necessary because key state may change between
#                                 # the two ifs
#                 if False:
#                     # Relative rel: URL
#                     locPath = self.editor.pWiki.getWikiConfigPath()
#                     if locPath is not None:
#                         locPath = dirname(locPath)
#                         relPath = relativeFilePath(locPath, fn)
#                         if relPath is None:
#                             # Absolute path needed
#                             urls.append("file:%s" % url)
#                         else:
#                             urls.append("rel://%s" % urllib.pathname2url(relPath))
#                 else:
    
                # Absolute file: URL
                url = "file:" + url
                
            self.getActiveEditor().AddText(url)
            self.configuration.set("main", "last_active_dir", dirname(dlg.GetPath()))
            
        dlg.Destroy()



    def showSpellCheckerDialog(self):
        if self.spellChkDlg != None:
            return
        try:
            self.spellChkDlg = SpellChecker.SpellCheckerDialog(self, -1, self)
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise

        self.spellChkDlg.CenterOnParent(wx.BOTH)
        self.spellChkDlg.Show()
        self.spellChkDlg.checkNext(startPos=0)


    def rebuildWiki(self, skipConfirm=False):
        if self.isReadOnlyWiki():
            return

        if not skipConfirm:
            result = wx.MessageBox(_(u"Are you sure you want to rebuild this wiki? "
                    u"You may want to backup your data first!"),
                    _(u'Rebuild wiki'),
                    wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION, self)

        if skipConfirm or result == wx.YES :
            try:
                self.saveAllDocPages()
                progresshandler = wxGuiProgressHandler(_(u"Rebuilding wiki"),
                        _(u"Rebuilding wiki"), 0, self)
                self.getWikiDataManager().rebuildWiki(progresshandler)

                self.tree.collapse()

                # TODO Adapt for functional pages
                if self.getCurrentWikiWord() is not None:
                    self.openWikiPage(self.getCurrentWikiWord(),
                            forceTreeSyncFromRoot=True)
                self.tree.expandRoot()
            except (IOError, OSError, DbAccessError), e:
                self.lostAccess(e)
                raise
            except Exception, e:
                self.displayErrorMessage(_(u"Error rebuilding wiki"), e)
                traceback.print_exc()


    def vacuumWiki(self):
        if self.isReadOnlyWiki():
            return

        try:
            self.getWikiData().vacuum()
        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise


#     def OnCmdCloneWindow(self, evt):
#         _prof.start()
#         self._OnCmdCloneWindow(evt)
#         _prof.stop()


    def OnCmdCloneWindow(self, evt):
        wd = self.getWikiDocument()
        if wd is None:
            return

        try:
            clAction = CmdLineAction([])
            clAction.wikiToOpen = wd.getWikiConfigPath()
            clAction.frameToOpen = 1  # Open in new frame
            wws = self.getMainAreaPanel().getOpenWikiWords()
            
            if wws is not None:
                clAction.wikiWordsToOpen = wws

            wx.GetApp().startPersonalWikiFrame(clAction)
        except Exception, e:
            traceback.print_exc()
            self.displayErrorMessage(_(u'Error while starting new '
                    u'WikidPad instance'), e)
            return


    def OnImportFromPagefiles(self, evt):
        if self.isReadOnlyWiki():
            return

        dlg=wx.MessageDialog(self,
                _(u"This could overwrite pages in the database. Continue?"),
                _(u"Import pagefiles"), wx.YES_NO)

        result = dlg.ShowModal()
        if result == wx.ID_YES:
            self.getWikiData().copyWikiFilesToDatabase()


    def OnCmdSwitchEditorPreview(self, evt):
        presenter = self.getCurrentDocPagePresenter()
        self.getMainAreaPanel().switchDocPagePresenterTabEditorPreview(presenter)

#         scName = presenter.getCurrentSubControlName()
#         if scName != "textedit":
#             presenter.switchSubControl("textedit", gainFocus=True)
#         else:
#             presenter.switchSubControl("preview", gainFocus=True)


    def insertAttribute(self, name, value, wikiWord=None):
        fmt = self.getFormatting()
        if fmt is None:
            return

        bs = fmt.BracketStart
        be = fmt.BracketEnd
        
        if wikiWord is None:
            self.getActiveEditor().AppendText(u"\n\n%s%s: %s%s" %
                    (bs, name, value, be))
        else:
            try:
                # self.saveCurrentDocPage()
                if self.getWikiDocument().isDefinedWikiWord(wikiWord):
                    page = self.getWikiDocument().getWikiPage(wikiWord)
                    page.appendLiveText(u"\n\n%s%s: %s%s" %
                            (bs, name, value, be))
            except (IOError, OSError, DbAccessError), e:
                self.lostAccess(e)
                raise


    def addText(self, text, replaceSel=False):
        """
        Add text to current active editor view
        """
        ed = self.getActiveEditor()
        ed.BeginUndoAction()
        try:
            if replaceSel:
                ed.ReplaceSelection(text)
            else:
                ed.AddText(text)
        finally:
            ed.EndUndoAction()


    def appendText(self, text):
        """
        Append text to current active editor view
        """
        ed = self.getActiveEditor()
        ed.BeginUndoAction()
        try:
            self.getActiveEditor().AppendText(text)
        finally:
            ed.EndUndoAction()


    def insertDate(self):
        if self.isReadOnlyPage():
            return

#         # strftime can't handle unicode correctly, so conversion is needed
#         mstr = mbcsEnc(self.configuration.get("main", "strftime"), "replace")[0]
#         self.getActiveEditor().AddText(mbcsDec(strftime(mstr), "replace")[0])

        mstr = self.configuration.get("main", "strftime")
        self.getActiveEditor().AddText(strftimeUB(mstr))

    def getLastActiveDir(self):
        return self.configuration.get("main", "last_active_dir", os.getcwd())

    
    def stdDialog(self, dlgtype, title, message, additional=None):
        """
        Used to show a dialog, especially in scripts.
        Possible values for dlgtype:
        "text": input text to dialog, additional is the default text
            when showing dlg returns entered text on OK or empty string
        "o": As displayMessage, shows only OK button
        "oc": Shows OK and Cancel buttons, returns either "ok" or "cancel"
        "yn": Yes and No buttons, returns either "yes" or "no"
        "ync": like "yn" but with additional cancel button, can also return
            "cancel"
        """
        if dlgtype == "text":
            if additional is None:
                additional = u""
            return guiToUni(wx.GetTextFromUser(uniToGui(message),
                    uniToGui(title), uniToGui(additional), self))
        else:
            style = None
            if dlgtype == "o":
                style = wx.OK
            elif dlgtype == "oc":
                style = wx.OK | wx.CANCEL
            elif dlgtype == "yn":
                style = wx.YES_NO
            elif dlgtype == "ync":
                style = wx.YES_NO | wx.CANCEL
            
            if style is None:
                raise RuntimeError, _(u"Unknown dialog type")

            result = wx.MessageBox(uniToGui(message), uniToGui(title), style, self)
            
            if result == wx.OK:
                return "ok"
            elif result == wx.CANCEL:
                return "cancel"
            elif result == wx.YES:
                return "yes"
            elif result == wx.NO:
                return "no"
                
            raise RuntimeError, _(u"Internal Error")

    def displayMessage(self, title, str):
        """pops up a dialog box,
        used by scripts only
        """
        dlg_m = wx.MessageDialog(self, uniToGui(u"%s" % str), title, wx.OK)
        dlg_m.ShowModal()
        dlg_m.Destroy()


    def displayErrorMessage(self, errorStr, e=u""):
        "pops up a error dialog box"
        exMessage = mbcsDec(str(e))[0]
        dlg_m = wx.MessageDialog(self, uniToGui(u"%s. %s." % (errorStr, exMessage)),
                'Error!', wx.OK)
        dlg_m.ShowModal()
        dlg_m.Destroy()
        try:
            self.statusBar.SetStatusText(uniToGui(errorStr), 0)
        except:
            pass


    def showAboutDialog(self):
        dlg = AboutDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def OnShowWikiInfoDialog(self, evt):
        dlg = WikiInfoDialog(self, -1, self)
        dlg.ShowModal()
        dlg.Destroy()


    # ----------------------------------------------------------------------------------------
    # Event handlers from here on out.
    # ----------------------------------------------------------------------------------------


    def miscEventHappened(self, miscevt):
        """
        Handle misc events
        """
        try:
            if miscevt.getSource() is self.getWikiDocument():
                # Event from wiki document aka wiki data manager
                if miscevt.has_key("deleted wiki page"):
                    wikiPage = miscevt.get("wikiPage")
                    # trigger hooks
                    self.hooks.deletedWikiWord(self,
                            wikiPage.getWikiWord())
    
#                     self.fireMiscEventProps(miscevt.getProps())
    
                elif miscevt.has_key("renamed wiki page"):
                    oldWord = miscevt.get("wikiPage").getWikiWord()
                    newWord = miscevt.get("newWord")

                    # trigger hooks
                    self.hooks.renamedWikiWord(self, oldWord, newWord)

#                 elif miscevt.has_key("updated wiki page"):
#                     # This was send from a WikiDocument(=WikiDataManager) object,
#                     # send it again to listening components
#                     self.fireMiscEventProps(miscevt.getProps())
            elif miscevt.getSource() is self.getMainAreaPanel():
                self.fireMiscEventProps(miscevt.getProps())
#                 if miscevt.has_key("changed current docpage presenter"):
#                     self.hooks.switchedToWikiWord(self, oldWord, newWord)

            # Depending on wiki-related or global func. page, the following
            # events come from document or application object

            if (miscevt.getSource() is self.getWikiDocument()) or \
                   (miscevt.getSource() is wx.GetApp()):
                if miscevt.has_key("reread text blocks needed"):
                    self.rereadTextBlocks()
                elif miscevt.has_key("reread personal word list needed"):
                    if self.spellChkDlg is not None:
                        self.spellChkDlg.rereadPersonalWordLists()
                elif miscevt.has_key("reread favorite wikis needed"):
                    self.rereadFavoriteWikis()
                elif miscevt.has_key("reread recent wikis needed"):
                    self.rereadRecentWikis()


        except (IOError, OSError, DbAccessError), e:
            self.lostAccess(e)
            raise


    def getDefDirForWikiOpenNew(self):
        """
        Return the appropriate default directory to start when user
        wants to create a new or open an existing wiki.
        """
        startDir = self.getConfig().get("main",
                "wikiOpenNew_defaultDir", u"")
        if startDir == u"":
            startDir = self.getWikiConfigPath()
            if startDir is None:
                startDir = self.getLastActiveDir()
            else:
                startDir = dirname(dirname(startDir))
        
        return startDir




    def OnWikiOpen(self, event):
        dlg = wx.FileDialog(self, _(u"Choose a Wiki to open"),
                self.getDefDirForWikiOpenNew(), "", "*.wiki", wx.OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            self.openWiki(mbcsDec(abspath(dlg.GetPath()), "replace")[0])
        dlg.Destroy()


    def OnWikiOpenNewWindow(self, event):
        dlg = wx.FileDialog(self, _(u"Choose a Wiki to open"),
                self.getDefDirForWikiOpenNew(), "", "*.wiki", wx.OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            try:
                clAction = CmdLineAction([])
                clAction.wikiToOpen = mbcsDec(abspath(dlg.GetPath()), "replace")[0]
                clAction.frameToOpen = 1  # Open in new frame
                wx.GetApp().startPersonalWikiFrame(clAction)
            except Exception, e:
                traceback.print_exc()
                self.displayErrorMessage(_(u'Error while starting new '
                        u'WikidPad instance'), e)
                return

        dlg.Destroy()


    def OnWikiOpenAsType(self, event):
        dlg = wx.FileDialog(self, _(u"Choose a Wiki to open"),
                self.getDefDirForWikiOpenNew(), "", "*.wiki", wx.OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            self.openWiki(mbcsDec(abspath(dlg.GetPath()), "replace")[0],
                    ignoreWdhName=True)
        dlg.Destroy()


    def OnWikiNew(self, event):
        dlg = wx.TextEntryDialog (self,
                _(u"Name for new wiki (must be in the form of a WikiWord):"),
                _(u"Create New Wiki"), u"MyWiki", wx.OK | wx.CANCEL)

        if dlg.ShowModal() == wx.ID_OK:
            wikiName = guiToUni(dlg.GetValue())
            wikiName = WikiFormatting.wikiWordToLabelForNewWiki(wikiName)
#             wikiName = wikiWordToLabel(wikiName)

            # make sure this is a valid wiki word
            if wikiName.find(u' ') == -1 and \
                    WikiFormatting.isNakedWikiWordForNewWiki(wikiName):

                dlg = wx.DirDialog(self, _(u"Directory to store new wiki"),
                        self.getDefDirForWikiOpenNew(),
                        style=wx.DD_DEFAULT_STYLE|wx.DD_NEW_DIR_BUTTON)
                if dlg.ShowModal() == wx.ID_OK:
#                     try:
                    self.newWiki(wikiName, dlg.GetPath())
#                     except IOError, e:
#                         self.displayErrorMessage(u'There was an error while '+
#                                 'creating your new Wiki.', e)
            else:
                self.displayErrorMessage(_(u"'%s' is an invalid wiki word. "
                u"There must be no spaces and mixed caps") % wikiName)

        dlg.Destroy()





    def OnIdle(self, evt):
        if not self.configuration.getboolean("main", "auto_save"):  # self.autoSave:
            return
        if self.getWikiDocument() is None or self.getWikiDocument().getWriteAccessFailed():
            # No automatic saving due to previous error
            return

        # check if the current wiki page needs to be saved
        if self.getCurrentDocPage():
            (saveDirtySince, updateDirtySince) = \
                    self.getCurrentDocPage().getDirtySince()
            if saveDirtySince is not None:
                currentTime = time()
                # only try and save if the user stops typing
                if (currentTime - self.getActiveEditor().lastKeyPressed) > \
                        self.autoSaveDelayAfterKeyPressed:
#                     if saveDirty:
                    if (currentTime - saveDirtySince) > \
                            self.autoSaveDelayAfterDirty:
                        self.saveAllDocPages()
#                     elif updateDirty:
#                         if (currentTime - self.currentWikiPage.lastUpdate) > 5:
#                             self.updateRelationships()

    def OnSize(self, evt):
        if self.windowLayouter is not None:
            self.windowLayouter.layout()
            if self.lowResources:
                self.resourceWakeup()



    def isReadOnlyWiki(self):
        wikiDoc = self.getWikiDocument()
        return (wikiDoc is None) or wikiDoc.isReadOnlyEffect()


    def isReadOnlyPage(self):
        docPage = self.getCurrentDocPage()
        return (docPage is None) or docPage.isReadOnlyEffect()
                


    def OnUpdateDisReadOnlyWiki(self, evt):
        """
        Called for ui-update to disable menu item if wiki is read-only.
        """
        evt.Enable(not self.isReadOnlyWiki())

    def OnUpdateDisReadOnlyPage(self, evt):
        """
        Called for ui-update to disable menu item if page is read-only.
        """
        evt.Enable(not self.isReadOnlyPage())

    def OnUpdateDisNotTextedit(self, evt):
        """
        Disables item if current presenter doesn't show textedit subcontrol.
        """
        pres = self.getCurrentDocPagePresenter()
        if pres is None or pres.getCurrentSubControlName() != "textedit":
            evt.Enable(False)

    def OnUpdateDisNotWikiPage(self, evt):
        """
        Disables item if current presenter doesn't show a real wiki page.
        """
        if self.getCurrentWikiWord() is None:
            evt.Enable(False)            
        

    def OnCmdCheckWrapMode(self, evt):        
        self.getActiveEditor().setWrapMode(evt.IsChecked())
        self.configuration.set("main", "wrap_mode", evt.IsChecked())

    def OnUpdateWrapMode(self, evt):
        evt.Check(self.getActiveEditor().getWrapMode())


    def OnCmdCheckIndentationGuides(self, evt):        
        self.getActiveEditor().SetIndentationGuides(evt.IsChecked())
        self.configuration.set("main", "indentation_guides", evt.IsChecked())

    def OnUpdateIndentationGuides(self, evt):
        evt.Check(self.getActiveEditor().GetIndentationGuides())


    def OnCmdCheckAutoIndent(self, evt):        
        self.getActiveEditor().setAutoIndent(evt.IsChecked())
        self.configuration.set("main", "auto_indent", evt.IsChecked())

    def OnUpdateAutoIndent(self, evt):
        evt.Check(self.getActiveEditor().getAutoIndent())


    def OnCmdCheckAutoBullets(self, evt):        
        self.getActiveEditor().setAutoBullets(evt.IsChecked())
        self.configuration.set("main", "auto_bullets", evt.IsChecked())

    def OnUpdateAutoBullets(self, evt):
        evt.Check(self.getActiveEditor().getAutoBullets())


    def OnCmdCheckTabsToSpaces(self, evt):        
        self.getActiveEditor().setTabsToSpaces(evt.IsChecked())
        self.configuration.set("main", "editor_tabsToSpaces", evt.IsChecked())

    def OnUpdateTabsToSpaces(self, evt):
        evt.Check(self.getActiveEditor().getTabsToSpaces())


    def OnCmdCheckShowLineNumbers(self, evt):        
        self.getActiveEditor().setShowLineNumbers(evt.IsChecked())
        self.configuration.set("main", "show_lineNumbers", evt.IsChecked())

    def OnUpdateShowLineNumbers(self, evt):
        evt.Check(self.getActiveEditor().getShowLineNumbers())


    def OnCmdCheckShowFolding(self, evt):        
        self.getActiveEditor().setFoldingActive(evt.IsChecked())
        self.configuration.set("main", "editor_useFolding", evt.IsChecked())

    def OnUpdateShowFolding(self, evt):
        evt.Check(self.getActiveEditor().getFoldingActive())


    def OnCloseButton(self, evt):
        if self.configuration.getboolean("main", "minimize_on_closeButton"):
            self.Iconize(True)
        else:
            try:
                self._prepareExitWiki()
                self.Destroy()
                evt.Skip()
            except LossyWikiCloseDeniedException:
                pass


    def exitWiki(self):
        self._prepareExitWiki()
        self.Destroy()

    def _prepareExitWiki(self):
#         if not self.configuration.getboolean("main", "minimize_on_closeButton"):
#             self.Close()
#         else:
#             self.prepareExit()
#             self.Destroy()
# 
# 
#     def prepareExit(self):
        # Stop clipboard catcher if running
#         if self.clipboardInterceptor is not None:
#             self.clipboardInterceptor.catchOff()

        if self._interceptCollection is not None:
            self._interceptCollection.close()

        self.getMainAreaPanel().updateConfig()
        self.closeWiki()

        wx.GetApp().getMiscEvent().removeListener(self)

        # if the frame is not minimized
        # update the size/pos of the global config
        if not self.IsIconized():
            curSize = self.GetSize()
            self.configuration.set("main", "size_x", curSize.x)
            self.configuration.set("main", "size_y", curSize.y)
            curPos = self.GetPosition()
            self.configuration.set("main", "pos_x", curPos.x)
            self.configuration.set("main", "pos_y", curPos.y)

        # windowmode:  0=normal, 1=maximized, 2=iconized, 3=maximized iconized

        windowmode = 0
        if self.IsMaximized():
            windowmode |= 1
        if self.IsIconized():
            windowmode |= 2

        self.configuration.set("main", "windowmode", windowmode)

        layoutCfStr = self.windowLayouter.getWinPropsForConfig()
        self.configuration.set("main", "windowLayout", layoutCfStr)

        self.configuration.set("main", "frame_stayOnTop", self.getStayOnTop())
        self.configuration.set("main", "zoom", self.getActiveEditor().GetZoom())
        self.configuration.set("main", "wiki_history", ";".join(self.wikiHistory))
        self.writeGlobalConfig()

        # trigger hook
        self.hooks.exit(self)

        self.getMainAreaPanel().close()

        # save the current wiki state
#         self.saveCurrentWikiState()

        wx.TheClipboard.Flush()

        if self.tbIcon is not None:
            if self.tbIcon.IsIconInstalled():
                self.tbIcon.RemoveIcon()

            self.tbIcon.Destroy()
            # May mysteriously prevent crash when closing WikidPad minimized
            #   on tray
            sleep(0.1)
            self.tbIcon = None
        
        wx.GetApp().unregisterMainFrame(self)



class TaskBarIcon(wx.TaskBarIcon):
    def __init__(self, pWiki):
        wx.TaskBarIcon.__init__(self)
        self.pWiki = pWiki

        # Register menu events
        wx.EVT_MENU(self, GUI_ID.TBMENU_RESTORE, self.OnLeftUp)
        wx.EVT_MENU(self, GUI_ID.TBMENU_SAVE,
                lambda evt: (self.pWiki.saveAllDocPages(),
                self.pWiki.getWikiData().commit()))
        wx.EVT_MENU(self, GUI_ID.TBMENU_EXIT, self.OnCmdExit)

        if self.pWiki.clipboardInterceptor is not None:
            wx.EVT_MENU(self, GUI_ID.CMD_CLIPBOARD_CATCHER_AT_CURSOR,
                    self.pWiki.OnClipboardCatcherAtCursor)
            wx.EVT_MENU(self, GUI_ID.CMD_CLIPBOARD_CATCHER_OFF,
                    self.pWiki.OnClipboardCatcherOff)

            wx.EVT_UPDATE_UI(self, GUI_ID.CMD_CLIPBOARD_CATCHER_AT_CURSOR,
                    self.pWiki.OnUpdateClipboardCatcher)
            wx.EVT_UPDATE_UI(self, GUI_ID.CMD_CLIPBOARD_CATCHER_OFF,
                    self.pWiki.OnUpdateClipboardCatcher)

        wx.EVT_TASKBAR_LEFT_UP(self, self.OnLeftUp)


    def OnCmdExit(self, evt):
        # Trying to prevent a crash with this
        wx.CallAfter(self.pWiki.exitWiki)


    def OnLeftUp(self, evt):
        if self.pWiki.IsIconized():
            self.pWiki.Iconize(False)
            self.pWiki.Show(True)
        
        self.pWiki.Raise()


    def CreatePopupMenu(self):
        tbMenu = wx.Menu()
        # Build menu
        if self.pWiki.clipboardInterceptor is not None:
            menuItem = wx.MenuItem(tbMenu,
                    GUI_ID.CMD_CLIPBOARD_CATCHER_AT_CURSOR,
                    _(u"Clipboard Catcher at Cursor"), u"", wx.ITEM_CHECK)
            tbMenu.AppendItem(menuItem)

            menuItem = wx.MenuItem(tbMenu, GUI_ID.CMD_CLIPBOARD_CATCHER_OFF,
                    _(u"Clipboard Catcher off"), u"", wx.ITEM_CHECK)
            tbMenu.AppendItem(menuItem)
            
            tbMenu.AppendSeparator()


        appendToMenuByMenuDesc(tbMenu, _SYSTRAY_CONTEXT_MENU_BASE)


        return tbMenu


def importCode(code, usercode, userUserCode, name, add_to_sys_modules=False):
    """
    Import dynamically generated code as a module. 
    usercode and code are the objects containing the code
    (a string, a file handle or an actual compiled code object,
    same types as accepted by an exec statement), usercode
    may be None. code is executed first, usercode thereafter
    and can overwrite settings in code. The name is the name to give to the module,
    and the final argument says wheter to add it to sys.modules
    or not. If it is added, a subsequent import statement using
    name will return this module. If it is not added to sys.modules
    import will try to load it in the normal fashion.

    import foo

    is equivalent to

    foofile = open("/path/to/foo.py")
    foo = importCode(foofile,"foo",1)

    Returns a newly generated module.
    """
    import sys,imp

    module = imp.new_module(name)

    exec code in module.__dict__
    if usercode is not None:
        exec usercode in module.__dict__
    if userUserCode is not None:
        exec userUserCode in module.__dict__
    if add_to_sys_modules:
        sys.modules[name] = module

    return module




_SYSTRAY_CONTEXT_MENU_BASE = \
u"""
Restore;TBMENU_RESTORE
Save;TBMENU_SAVE
Exit;TBMENU_EXIT
"""


# Entries to support i18n of context menus

N_(u"Restore")
N_(u"Save")
N_(u"Exit")

# _TASKBAR_CONTEXT_MENU_CLIPCATCH = \
# u"""
# Clipboard Catcher at Cursor;CMD_CLIPBOARD_CATCHER_AT_CURSOR
# Clipboard Catcher off;CMD_CLIPBOARD_CATCHER_OFF
# -
# """




#         This function must FOLLOW the actual update eventhandler in the
#         updatefct tuple of self.addMenuItem.