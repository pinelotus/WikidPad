from weakref import WeakValueDictionary
import os, os.path, sets, time, shutil, traceback
from threading import RLock, Thread
from pwiki.rtlibRepl import re  # Original re doesn't work right with
        # multiple threads   (TODO!)

from wx import GetApp

from pwiki.MiscEvent import MiscEventSourceMixin

from pwiki.WikiExceptions import *
from pwiki.StringOps import mbcsDec, re_sub_escape, pathEnc, pathDec
from pwiki.DocPages import WikiPage, FunctionalPage, AliasWikiPage

import pwiki.PageAst as PageAst


# from pwiki.Configuration import createWikiConfiguration
from pwiki.WikiFormatting import WikiFormatting

from pwiki.SearchAndReplace import SearchReplaceOperation

import DbBackendUtils, FileStorage



_openDocuments = {}  # Dictionary {<path to data dir>: <WikiDataManager>}


_globalFuncPages = WeakValueDictionary()  # weak dictionary
        # {<funcTag starting with "global/">: <funcPage>}

def isDbHandlerAvailable(dbtype):
    wikiDataFactory, createWikiDbFunc = DbBackendUtils.getHandler(dbtype)
    return wikiDataFactory is not None


def createWikiDb(pWiki, dbtype, wikiName, dataDir, overwrite=False):
    """
    Create a new wiki database
    pWiki -- instance of PersonalWikiFrame
    dbtype -- internal name of database type
    wikiName -- Name of the wiki to create
    dataDir -- directory for storing the data files
    overwrite -- Should already existing data be overwritten?
    """
    global _openDocuments

    wdm = _openDocuments.get(dataDir)
    if wdm is not None:
        raise WikiDBExistsException(
                _(u"Database exists already and is currently in use"))

    wikiDataFactory, createWikiDbFunc = DbBackendUtils.getHandler(dbtype)
    if wikiDataFactory is None:
        raise NoDbHandlerException(
                _(u"Data handler %s not available") % dbtype)

    createWikiDbFunc(wikiName, dataDir, overwrite)


def openWikiDocument(wikiConfigFilename, wikiSyntax, dbtype=None,
        ignoreLock=False, createLock=True):
    """
    Create a new instance of the WikiDataManager or return an already existing
    one
    dbtype -- internal name of database type
    wikiName -- Name of the wiki to create
    dataDir -- directory for storing the data files
    overwrite -- Should already existing data be overwritten
    """
    global _openDocuments

    wdm = _openDocuments.get(wikiConfigFilename)
    if wdm is not None:
        if dbtype is not None and dbtype != wdm.getDbtype():
            # Same database can't be opened twice with different db handlers
            raise WrongDbHandlerException(_(u"Database is already in use "
                    u"with handler '%s'. Can't open with different handler.") %
                    wdm.getDbtype())

        wdm.incRefCount()
        return wdm

    wdm = WikiDataManager(wikiConfigFilename, wikiSyntax, dbtype, ignoreLock,
            createLock)

    _openDocuments[wikiConfigFilename] = wdm

    return wdm



#     wikiDataFactory, createWikiDbFunc = DbBackendUtils.getHandler(pWiki, dbtype)
#     if wikiDataFactory is None:
#         raise NoDbHandlerException("Data handler %s not available" % dbtype)
#
#     wd = wikiDataFactory(pWiki, dataDir)
#     return WikiDataManager(pWiki, wd, dbtype)


def splitConfigPathAndWord(wikiCombinedFilename):
    """
    wikiCombinedFilename -- Path of config filename or possibly name of a wiki file

    return: tuple (cfg, wikiword) with cfg real config filepath (None if it
            couldn't be found. wikiword is the wikiword to jump to or None
    """
    wikiConfig = GetApp().createWikiConfiguration()
    if os.path.supports_unicode_filenames:
        wikiConfigFilename = mbcsDec(wikiCombinedFilename)[0]
    else:
        wikiConfigFilename = wikiCombinedFilename
    wikiWord = None

    while True:
        try:
            # config.read(wikiConfigFile)
            wikiConfig.loadConfig(wikiConfigFilename)
            return wikiConfigFilename, wikiWord
        except Exception, e:
            # try to recover by checking if the parent dir contains the real wiki file
            # if it does the current wiki file must be a wiki word file, so open the
            # real wiki to the wiki word.
#                 try:
            parentDir = os.path.dirname(os.path.dirname(wikiConfigFilename))
            if parentDir:
                try:
                    wikiFiles = [file for file in os.listdir(parentDir) \
                            if file.endswith(".wiki")]
                    if len(wikiFiles) > 0:
                        wikiWord = os.path.basename(wikiConfigFilename)
                        wikiWord = wikiWord[0 : len(wikiWord) - 5]

                        # if this is win95 or < the file name could be a 8.3 alias, file~1 for example
                        windows83Marker = wikiWord.find("~")
                        if windows83Marker != -1:
                            wikiWord = wikiWord[0:windows83Marker]
                            matchingFiles = [file for file in wikiFiles \
                                    if file.lower().startswith(wikiWord)]
                            if matchingFiles:
                                wikiWord = matchingFiles[0]
                        wikiConfigFilename = os.path.join(parentDir, wikiFiles[0])
                        continue
                except (WindowsError, IOError, OSError):
                    # something went wrong -> give up
                    traceback.print_exc()
                    return None, None

            return None, None
    
    
def getGlobalFuncPage(funcTag):
    global _globalFuncPages
    
    if len(funcTag) == 0:
        return None  # TODO throw exception?
    
    if not funcTag.startswith(u"global/"):
        return None  # TODO throw exception?

    value = _globalFuncPages.get(funcTag)
    if value is None:
        value = FunctionalPage(None, funcTag)
        _globalFuncPages[funcTag] = value

    return value


# TODO Remove this hackish solution

class WikiDataSynchronizedFunction:
    def __init__(self, lock, function):
        self.accessLock = lock
        self.callFunction = function

    def __call__(self, *args, **kwargs):
        self.accessLock.acquire()
        try:
#             print "WikiDataSynchronizedFunction", repr(self.callFunction), repr(args)
            return self.callFunction(*args, **kwargs)
        finally:
            self.accessLock.release()


class WikiDataSynchronizedProxy:
    """
    Proxy class for synchronized access to a WikiData instance
    """
    def __init__(self, wikiData):
        self.wikiData = wikiData
        self.accessLock = RLock()
#         self.syncCommit = WikiDataSynchronizedFunction(self.accessLock,
#                 getattr(self.wikiData, "commit"))
#         self.commit = self.asyncCommit
# 
#     def asyncCommit(self):
#         Thread(target=self.syncCommit).start()

    def __getattr__(self, attr):
        result = WikiDataSynchronizedFunction(self.accessLock,
                getattr(self.wikiData, attr))
                
        self.__dict__[attr] = result

        return result


class WikiDataManager(MiscEventSourceMixin):
    """
    Wraps a WikiData object and provides services independent
    of database backend, especially creation of WikiPage objects.

    When the open wiki database changes, a new DataManager is created.

    When asking for a WikiPage for the same word twice and the first object
    exists yet, no new object is created, but the same returned.

    WikiDataManager holds internally a reference count to know how many
    PersonalWikiFrame instances refer to it. Call release() to
    decrement the refcount. If it goes to zero, the wrapped WikiData
    instance will be closed. The refcount starts with 1 when creating
    a WikiDataManager instance.
    """

    def __init__(self, wikiConfigFilename, wikiSyntax, dbtype, ignoreLock=False,
            createLock=True):
        MiscEventSourceMixin.__init__(self)

        self.lockFileName = wikiConfigFilename + u".lock"
        if not ignoreLock and os.path.exists(pathEnc(self.lockFileName)):
            raise LockedWikiException(
                    _(u"Wiki is probably already in use by other instance"))

        if createLock:
            try:
                f = open(pathEnc(self.lockFileName), "w")
                self.writeAccessDenied = False
                f.close()
            except IOError:
                self.lockFileName = None
                self.writeAccessDenied = True
        else:
            self.lockFileName = None

        wikiConfig = GetApp().createWikiConfiguration()
        self.connected = False
        self.readAccessFailed = False
        self.writeAccessFailed = False
        self.writeAccessDenied = False

        wikiConfig.loadConfig(wikiConfigFilename)

        # config variables
        wikiName = wikiConfig.get("main", "wiki_name")
        dataDir = wikiConfig.get("wiki_db", "data_dir")

        # except Exception, e:
        if wikiName is None or dataDir is None:
            self._releaseLockFile()
            raise BadConfigurationFileException(
                    _(u"Wiki configuration file is corrupted"))
        
        # os.access does not answer reliably if file is writable
        # (at least on Windows), therefore we have to just open it
        # in writable mode
        try:
            f = open(pathEnc(wikiConfigFilename), "r+b")
            self.writeAccessDenied = False
            f.close()
        except IOError:
            self.writeAccessDenied = True

        self.wikiConfiguration = wikiConfig

        wikiConfig.setWriteAccessDenied(self.writeAccessDenied or
                self.getWriteAccessDeniedByConfig())

        # absolutize the path to data dir if it's not already
        if not os.path.isabs(dataDir):
            dataDir = os.path.join(os.path.dirname(wikiConfigFilename), dataDir)

#         dataDir = mbcsDec(os.path.abspath(dataDir), "replace")[0]
        dataDir = pathDec(os.path.abspath(dataDir))

#         self.wikiConfigFilename = wikiConfigFilename

        if not dbtype:
            wikidhName = wikiConfig.get("main",
                    "wiki_database_type", "")
        else:
            wikidhName = dbtype

        if not wikidhName:
            # Probably old database version without handler tag
            self._releaseLockFile()
            raise UnknownDbHandlerException(
                    _(u'No data handler information found, probably '
                    u'"Original Gadfly" is right.'))

        if not isDbHandlerAvailable(wikidhName):
            self._releaseLockFile()
            raise DbHandlerNotAvailableException(
                    _(u'Required data handler %s unknown to WikidPad') % wikidhName)

        wikiDataFactory, createWikiDbFunc = DbBackendUtils.getHandler(wikidhName)
        if wikiDataFactory is None:
            self._releaseLockFile()
            raise NoDbHandlerException(
                    _(u"Required data handler %s not available") % wikidhName)

        self.ensureWikiTempDir()
        wikiData = wikiDataFactory(self, dataDir, self.getWikiTempDir())

        self.baseWikiData = wikiData
        self.autoLinkRelaxRE = None
        self.wikiData = WikiDataSynchronizedProxy(self.baseWikiData)
        self.wikiPageDict = WeakValueDictionary()
        self.funcPageDict = WeakValueDictionary()

        self.wikiName = wikiName
        self.dataDir = dataDir
        self.dbtype = wikidhName

        self.refCount = 1
        
        self.formatting = WikiFormatting(self, wikiSyntax)  # TODO wikiSyntax


    
    def checkDatabaseFormat(self):
        """
        Returns a pair (<frmcode>, <plain text>) where frmcode is an integer
        and means:
        0: Up to date,  1: Update needed,  2: Unknown format, update not possible
        """
        return self.wikiData.checkDatabaseFormat()


    def connect(self):
        # Connect might be called too often, so check if it was already done
        if self.connected:
            return

        writeException = None
        try:
            self.wikiData.connect()
        except DbWriteAccessError, e:
            traceback.print_exc()
            writeException = e

        # Path to file storage
        fileStorDir = os.path.join(os.path.dirname(self.getWikiConfigPath()),
                "files")

        self.fileStorage = FileStorage.FileStorage(self, fileStorDir)

        # Set file storage according to configuration
        fs = self.fileStorage

        fs.setModDateMustMatch(self.getWikiConfig().getboolean("main",
                "fileStorage_identity_modDateMustMatch", False))
        fs.setFilenameMustMatch(self.getWikiConfig().getboolean("main",
                "fileStorage_identity_filenameMustMatch", False))
        fs.setModDateIsEnough(self.getWikiConfig().getboolean("main",
                "fileStorage_identity_modDateIsEnough", False))

        self.wikiConfiguration.getMiscEvent().addListener(self)
        GetApp().getMiscEvent().addListener(self)

        self.getFormatting().rebuildFormatting(None)
        self._updateCcWordBlacklist()
        
        self.readAccessFailed = False
        self.writeAccessFailed = False
        self.noAutoSaveFlag = False # Flag is set (by PersonalWikiFrame),
                # if some error occurred during saving and the user doesn't want
                # to retry saving. WikiDataManager does not change or respect
                # this flag.
                
        self.autoReconnectTriedFlag = False
        
        self.connected = True
        
        if writeException:
            self.writeAccessFailed = True
            raise writeException


    def incRefCount(self):
        self.refCount += 1
        return self.refCount

    def _releaseLockFile(self):
        """
        Release lock file if it was created before
        """
        if self.lockFileName is not None:
            try:
                os.unlink(self.lockFileName)
            except:
                traceback.print_exc()


    def release(self):
        """
        Inform this instance that it is no longer needed by one of the
        holding PersonalWikiFrame objects.
        Decrements the internal refcounter, if it goes to zero, the used
        WikiData instance is closed.
        
        Don't call any other method on the instance after calling this method.
        """
        global _openDocuments

        self.refCount -= 1

        if self.refCount <= 0:
            wikiTempDir = self.getWikiTempDir()

            if self.wikiData is not None:
                self.wikiData.close()
                self.wikiData = None
                self.baseWikiData = None

            GetApp().getMiscEvent().removeListener(self)

            del _openDocuments[self.getWikiConfig().getConfigPath()]

            self._releaseLockFile()

            if wikiTempDir is not None:
                # Warning!!! rmtree() is very dangerous, don't make a mistake here!
                shutil.rmtree(wikiTempDir, True)

        return self.refCount


    def getDbtype(self):
        return self.dbtype

    def getWikiData(self):
        return self.wikiData

    def getFileStorage(self):
        return self.fileStorage
        
    def getWikiConfig(self):
        return self.wikiConfiguration
        
    def getWikiConfigPath(self):
        return self.wikiConfiguration.getConfigPath()
        
    def getWikiPath(self):
        return os.path.dirname(self.getWikiConfigPath())        

    def getFormatting(self):
        return self.formatting
        
    def getWikiName(self):
        return self.wikiName
        
    def getDataDir(self):
        return self.dataDir
        
    def getCollator(self):
        return GetApp().getCollator()
        
    def getWikiTempDir(self):
#         if GetApp().getGlobalConfig().getboolean("main", "tempFiles_inWikiDir",
#                 False) and not self.isReadOnlyEffect():
#             return os.path.join(os.path.dirname(self.getWikiConfigPath()),
#                     "temp")
#         else:

        # Warning! The returned directory will be deleted with shutil.rmtree when the wiki is
        # finally released!
        return None


    def ensureWikiTempDir(self):
        """
        Try to ensure existence of wiki temp directory
        """
        tempDir = self.getWikiTempDir()
        
        if tempDir is not None:
            try:
                os.makedirs(tempDir)
            except OSError:
                self.setReadAccessFailed(True)

    def getNoAutoSaveFlag(self):
        """
        Flag is set (by PersonalWikiFrame),
        if some error occurred during saving and the user doesn't want
        to retry saving. WikiDataManager does not change or respect
        this flag.
        """
        return self.noAutoSaveFlag
        
    def setNoAutoSaveFlag(self, val):
        self.noAutoSaveFlag = val
        # TODO send message?


    def getReadAccessFailed(self):
        """
        Flag is set (by PersonalWikiFrame),
        """
        return self.readAccessFailed
        
    def setReadAccessFailed(self, val):
        self.readAccessFailed = val
        # TODO send message?


    def getWriteAccessFailed(self):
        """
        Flag is set (by PersonalWikiFrame),
        """
        return self.writeAccessFailed
        
    def setWriteAccessFailed(self, val):
        self.writeAccessFailed = val
        # TODO send message?
        
    def getWriteAccessDenied(self):
        """
        Flag is set (by PersonalWikiFrame),
        """
        return self.writeAccessDenied
        
    def getWriteAccessDeniedByConfig(self):
        return self.getWikiConfig().getboolean("main", "wiki_readOnly")


    def setWriteAccessDeniedByConfig(self, newValue):
        wikiConfig = self.getWikiConfig()

        if wikiConfig.getboolean("main", "wiki_readOnly") == newValue:
            return

        if self.writeAccessFailed or self.writeAccessDenied:
            return  # Don't touch if readonly for other reasons

        if newValue:
            wikiConfig.set("main", "wiki_readOnly", "True")
            wikiConfig.setWriteAccessDenied(True)
        else:
            wikiConfig.setWriteAccessDenied(False)
            wikiConfig.set("main", "wiki_readOnly", "False")


    def isReadOnlyEffect(self):
        """
        Return true if underlying wiki is effectively read-only, this means
        "for any reason", regardless if error or intention.
        """
        return self.writeAccessFailed or self.writeAccessDenied or \
                self.getWriteAccessDeniedByConfig()


    def getAutoReconnectTriedFlag(self):
        """
        Flag is set (by PersonalWikiFrame),
        if after some read/write error the program already tried to reconnect
        to database and should not automatically try again, only on user
        request.
        """
        return self.autoReconnectTriedFlag
        
    def setAutoReconnectTriedFlag(self, val):
        self.autoReconnectTriedFlag = val
        # TODO send message?


    def isDefinedWikiWord(self, wikiWord):
        return self.wikiData.isDefinedWikiWord(wikiWord)
        
        
    def isCreatableWikiWord(self, wikiWord):
        """
        Returns True if wikiWord can be created in the database. Does not
        check against regular expression, but checks if word already
        exists or (if document is in caseless mode) if word with different
        case but same content already exists.
        If this returns False, self.getAliasesWikiWord(wikiWord) must be able to
        return an existing word whose existence prevents creation of wikiWord

        TODO: Check against existing aliases
        """
        # TODO: Caseless mode
        return not self.wikiData.isDefinedWikiWord(wikiWord)


    def getNormcasedWikiWord(self, word):
        """
        Get normcased version of word. It isn't checked if word exists.
        Currently this function just calls word.lower().
        """
        return word.lower()


    def getWikiPage(self, wikiWord):
        """
        Fetch a WikiPage for the wikiWord, throws WikiWordNotFoundException
        if word doesn't exist
        """
        if not self.wikiData.isDefinedWikiWord(wikiWord):
            raise WikiWordNotFoundException(
                    _(u"Word '%s' not in wiki") % wikiWord)

        return self.getWikiPageNoError(wikiWord)

    def getWikiPageNoError(self, wikiWord):
        """
        fetch a WikiPage for the wikiWord. If it doesn't exist, return
        one without throwing an error and without updating the cache.

        Asking for the same wikiWord twice returns the same object if
        it wasn't garbage collected yet.
        """
        value = self.wikiPageDict.get(wikiWord)

        if value is not None and isinstance(value, AliasWikiPage):
            # Check if existing alias page is up to date
            realWikiWord1 = value.getNonAliasPage().getWikiWord()
            realWikiWord2 = self.wikiData.getAliasesWikiWord(wikiWord)

            if realWikiWord1 != realWikiWord2:
                # if not, retrieve new page
                value = None

        if value is None:
            # No active page available
            realWikiWord = self.wikiData.getAliasesWikiWord(wikiWord)
            if wikiWord == realWikiWord:
                # no alias
                value = WikiPage(self, wikiWord)
            else:
#                 realpage = WikiPage(self, realWikiWord)
                realpage = self.getWikiPageNoError(realWikiWord)
                value = AliasWikiPage(self, wikiWord, realpage)

            self.wikiPageDict[wikiWord] = value

            value.getMiscEvent().addListener(self)

        return value


    def _getWikiPageNoErrorNoCache(self, wikiWord):
        """
        Similar to getWikiPageNoError, but does not save retrieved
        page in cache if it isn't there yet.
        """
        value = self.wikiPageDict.get(wikiWord)

        if value is not None and isinstance(value, AliasWikiPage):
            # Check if existing alias page is up to date
            realWikiWord1 = value.getNonAliasPage().getWikiWord()
            realWikiWord2 = self.wikiData.getAliasesWikiWord(wikiWord)
            
            if realWikiWord1 != realWikiWord2:
                # if not, retrieve new page
                value = None
        
        if value is None:
            # No active page available
            realWikiWord = self.wikiData.getAliasesWikiWord(wikiWord)
            if wikiWord == realWikiWord:
                # no alias
                value = WikiPage(self, wikiWord)
            else:
#                 realpage = WikiPage(self, realWikiWord)
                realpage = self.getWikiPageNoError(realWikiWord)
                value = AliasWikiPage(self, wikiWord, realpage)

        return value



    def createWikiPage(self, wikiWord, suggNewPageTitle=None):
        """
        Create a new wikiPage for the wikiWord.
        suggNewPageTitle -- if not None contains the title of the page to create
                (without syntax specific prefix).
        """
        page = self.getWikiPageNoError(wikiWord)
        page.setSuggNewPageTitle(suggNewPageTitle)
        return page


    def getFuncPage(self, funcTag):
        """
        Retrieve a functional page
        """
        global _globalFuncPages
        if funcTag.startswith(u"global/"):
            value = getGlobalFuncPage(funcTag)
        else:
            value = self.funcPageDict.get(funcTag)
            if value is None:
                value = FunctionalPage(self, funcTag)
                self.funcPageDict[funcTag] = value

        if not value.getMiscEvent().hasListener(self):
            value.getMiscEvent().addListener(self)

        return value


    def rebuildWiki(self, progresshandler):
        """
        Rebuild  the wiki

        progresshandler -- Object, fulfilling the
            PersonalWikiFrame.GuiProgressHandler protocol
        """
        formatting = self.getFormatting()

        self.getWikiData().refreshDefinedContentNames()

        # get all of the wikiWords
        wikiWords = self.getWikiData().getAllDefinedWikiPageNames()

        progresshandler.open(len(wikiWords) * 2 + 1)

        # re-save all of the pages
        try:
            step = 1

            self.getWikiData().clearCacheTables()
            
            # Step one: update properties. There may be properties which
            #   define how the rest has to be interpreted, therefore they
            #   must be processed first.
            for wikiWord in wikiWords:
                progresshandler.update(step, _(u"Update attributes"))   # , "Rebuilding %s" % wikiWord)
                try:
                    wikiPage = self._getWikiPageNoErrorNoCache(wikiWord)
                    if isinstance(wikiPage, AliasWikiPage):
                        # This should never be an alias page, so fetch the
                        # real underlying page
                        # This can only happen if there is a real page with
                        # the same name as an alias
                        wikiPage = WikiPage(self, wikiWord)

                    pageAst = PageAst.Page()
                    pageAst.buildAst(formatting, wikiPage.getContent(),
                            wikiPage.getFormatDetails())

                    wikiPage.refreshPropertiesFromPageAst(pageAst)
                except:
                    traceback.print_exc()

                step = step + 1

            # Step two: update the rest (todos, relations)
            for wikiWord in wikiWords:
                progresshandler.update(step, _(u"Update pages"))   # , "Rebuilding %s" % wikiWord)
                try:
                    wikiPage = self._getWikiPageNoErrorNoCache(wikiWord)
                    if isinstance(wikiPage, AliasWikiPage):
                        # This should never be an alias page, so fetch the
                        # real underlying page
                        # This can only happen if there is a real page with
                        # the same name as an alias
                        wikiPage = WikiPage(self, wikiWord)

                    pageAst = PageAst.Page()
                    pageAst.buildAst(formatting, wikiPage.getContent(),
                            wikiPage.getFormatDetails())

                    wikiPage.refreshMainDbCacheFromPageAst(pageAst)
                except:
                    traceback.print_exc()

                step = step + 1

            progresshandler.update(step - 1, u"Final cleanup")
            # Give possibility to do further reorganisation
            # specific to database backend
            self.getWikiData().cleanupAfterRebuild(progresshandler)

        finally:
            progresshandler.close()


    def renameWikiWord(self, wikiWord, toWikiWord, modifyText):
        """
        modifyText -- Should the text of links to the renamed page be
                modified? This text replacement works unreliably
        """
        global _openDocuments
        
        try:        
            oldWikiPage = self.getWikiPage(wikiWord)
        except WikiWordNotFoundException:
            # So create page first
            oldWikiPage = self.createWikiPage(wikiWord)
            oldWikiPage.save(oldWikiPage.getLiveText())
            oldWikiPage.update(oldWikiPage.getLiveText())

        self.getWikiData().renameWord(wikiWord, toWikiWord)
        
        # TODO: Replace always?
        
        # Check if replacing previous title of page with new one

        # Prefix is normally u"++"
        pageTitlePrefix = self.getFormatting().getPageTitlePrefix() + u" "
        wikiWordTitle = self.getWikiPageTitle(wikiWord)
        
        if wikiWordTitle is not None:
            prevTitle = pageTitlePrefix + self.getWikiPageTitle(wikiWord) + u"\n"
        else:
            prevTitle = None
            
#         page = self.getWikiPage(toWikiWord)
#         content = page.getLiveText()
#         if prevTitle is not None and content.startswith(prevTitle):
#             # Replace previous title with new one
#             content = pageTitlePrefix + self.getWikiPageTitle(toWikiWord) + \
#                     u"\n" + content[len(prevTitle):]
#             page.replaceLiveText(content)

        # if the root was renamed we have a little more to do
        if wikiWord == self.getWikiName():
            wikiConfig = self.getWikiConfig()
            wikiConfig.set("main", "wiki_name", toWikiWord)
            wikiConfig.set("main", "last_wiki_word", toWikiWord)
            wikiConfig.save()

            wikiConfigPath = wikiConfig.getConfigPath()
            # Unload wiki configuration file
            wikiConfig.loadConfig(None)

            # Rename config file
            renamedConfigPath = os.path.join(
                    os.path.dirname(wikiConfigPath),
                    u"%s.wiki" % toWikiWord)
            os.rename(wikiConfigPath, renamedConfigPath)

            # Load it again
            wikiConfig.loadConfig(renamedConfigPath)
            self.wikiName = toWikiWord
            
            # Update dict of open documents (= wiki data managers)
            del _openDocuments[wikiConfigPath]
            _openDocuments[renamedConfigPath] = self

        oldWikiPage.informRenamedWikiPage(toWikiWord)
        del self.wikiPageDict[wikiWord]

        if modifyText:
            # now we have to search the wiki files and replace the old word with the new
            sarOp = SearchReplaceOperation()
            sarOp.wikiWide = True
            sarOp.wildCard = 'regex'
            sarOp.caseSensitive = True
            sarOp.searchStr = ur"\b" + re.escape(wikiWord) + ur"\b"
            
            for resultWord in self.searchWiki(sarOp):
                wikiPage = self.getWikiPage(resultWord)
                text = wikiPage.getLiveTextNoTemplate()
                if text is None:
                    continue

                sarOp.replaceStr = re_sub_escape(toWikiWord)
                sarOp.replaceOp = True
                sarOp.cycleToStart = False

                charStartPos = 0
    
                while True:
                    found = sarOp.searchText(text, charStartPos)
                    start, end = found[:2]
                    
                    if start is None: break
                    
                    repl = sarOp.replace(text, found)
                    text = text[:start] + repl + text[end:]  # TODO Faster?
                    charStartPos = start + len(repl)

                wikiPage.replaceLiveText(text)
                wikiPage.update(text)


        # Now we modify the page heading if not yet done by text replacing
        page = self.getWikiPage(toWikiWord)
        content = page.getLiveText()
        if prevTitle is not None and content.startswith(prevTitle):
            # Replace previous title with new one
            content = pageTitlePrefix + self.getWikiPageTitle(toWikiWord) + \
                    u"\n" + content[len(prevTitle):]
            page.replaceLiveText(content)
            page.update(content)


#     _AUTO_LINK_RELAX_SPLIT_RE = re.compile(r"[\W]", re.I | re.U)

    def _createAutoLinkRelaxWordEntryRE(self, word):
        """
        Get compiled regular expression for one word in autoLink "relax"
        mode
        """
        # Split into parts of contiguous alphanumeric characters
        parts = self.formatting.AutoLinkRelaxSplitRE.split(word)
        # Filter empty parts
        parts = [p for p in parts if p != u""]

        # Instead of original non-alphanum characters allow arbitrary
        # non-alphanum characters
        pat = ur"\b" + (self.formatting.AutoLinkRelaxJoinPAT.join(parts)) + ur"\b"
        regex = re.compile(pat, self.formatting.AutoLinkRelaxJoinFlags)

        return regex

#     _createAutoLinkRelaxWordEntryRE = staticmethod(
#             _createAutoLinkRelaxWordEntryRE)


    # TODO threadholder?
    def getAutoLinkRelaxRE(self):
        """
        Get regular expressions and words used to operate autoLink function in 
        "relax" mode
        """
        if self.autoLinkRelaxRE is None:
            # Build up regular expression
            # First fetch all wiki words
            words = self.getWikiData().getAllDefinedWikiPageNames() + \
                    self.getWikiData().getAllAliases()

            # Sort longest words first
            words.sort(key=lambda w: len(w), reverse=True)

            self.autoLinkRelaxRE = [
                    (self._createAutoLinkRelaxWordEntryRE(w), w)
                    for w in words]

        return self.autoLinkRelaxRE


    def getWikiPageTitle(self, wikiWord):
        """
        Return a title for a newly created page. It may return None if no title
        should be shown.
        """
        creaMode = self.getWikiConfig().getint("main",
                "wikiPageTitle_creationMode", 1)
        if creaMode == 0:
            # Let wikiword untouched
            return wikiWord
        elif creaMode == 1:
            # Add spaces before uppercase letters,
            # e.g. NewWikiWord -> New Wiki Word
            title = re.sub(ur'([A-Z\xc0-\xde]+)([A-Z\xc0-\xde][a-z\xdf-\xff])',
                    r'\1 \2', wikiWord)
            title = re.sub(ur'([a-z\xdf-\xff])([A-Z\xc0-\xde])', r'\1 \2',
                    title)
            return title
        else:  # creaMode == 2: No title at all.
            return None


    def searchWiki(self, sarOp, applyOrdering=True):  # TODO Threadholder
        """
        Search all wiki pages using the SearchAndReplaceOperation sarOp and
        return list of all page names that match the search criteria.
        If applyOrdering is True, the ordering of the sarOp is applied before
        returning the list.
        """
        wikiData = self.getWikiData()
        sarOp.beginWikiSearch(self)
        try:
            # First search currently cached pages
            exclusionSet = sets.Set()
            preResultSet = sets.Set()
            
            for k in self.wikiPageDict.keys():
                wikiPage = self.wikiPageDict.get(k)
                if wikiPage is None:
                    continue
                if isinstance(wikiPage, AliasWikiPage):
                    # Avoid to rename same page twice (alias and real) or more often
                    continue
                    
                text = wikiPage.getLiveTextNoTemplate()
                if text is None:
                    continue

                if sarOp.testWikiPage(k, text) == True:
                    preResultSet.add(k)

                exclusionSet.add(k)

            # Now search database
            resultSet = self.getWikiData().search(sarOp, exclusionSet)
            resultSet |= preResultSet
            if applyOrdering:
                result = sarOp.applyOrdering(resultSet, self.getCollator())
            else:
                result = list(resultSet)

        finally:
            sarOp.endWikiSearch()
            
        return result


    def getWikiWordsModifiedWithin(self, startTime, endTime):
        """
        startTime and endTime are floating values as returned by time.time()
        startTime is inclusive, endTime is exclusive
        """
        return self.getWikiData().getWikiWordsModifiedWithin(startTime, endTime)


    def getWikiWordsModifiedLastDays(self, days):
        """
        Return wiki words modified during the last number of days.
        """
        endTime = time.time()
        startTime = float(endTime-(86400*days))
        
        return self.getWikiData().getWikiWordsModifiedWithin(startTime, endTime)


    def _updateCcWordBlacklist(self):
        """
        Update the blacklist of camelcase words which should show up as normal
        text.
        """
        pg = self.getFuncPage("global/[CCBlacklist]")
        bls = sets.Set(pg.getLiveText().split("\n"))
        pg = self.getFuncPage("wiki/[CCBlacklist]")
        bls.union_update(pg.getLiveText().split("\n"))
        self.getFormatting().setCcWordBlacklist(bls)
        

    def getAliasesWikiWord(self, word):
        # TODO: Resolve properly in caseless mode
        return self.getWikiData().getAliasesWikiWord(word)

    def filterAliasesWikiWord(self, word):
        """
        Returns None if word doesn't exist,
        returns word if word isn't an alias,
        return the real word if word is an alias.
        """
        if not self.isDefinedWikiWord(word):
            return None
        
        return self.getAliasesWikiWord(word)


    def getPropertyTriples(self, word, key, value):
        return self.getWikiData().getPropertyTriples(word, key, value)


    def reconnect(self):
        """
        Closes current WikiData instance and opens a new one with the same
        settings. This should be called if connection was interrupted by a network
        problem or similar issues.
        """
        try:
            if self.wikiData is not None:
                self.wikiData.close()
        except:
            traceback.print_exc()

        self.autoReconnectTriedFlag = True
            
        self.wikiData = None
        self.baseWikiData = None
        self.autoLinkRelaxRE = None

        wikiDataFactory, createWikiDbFunc = DbBackendUtils.getHandler(self.dbtype)
        if wikiDataFactory is None:
            raise NoDbHandlerException(
                    _(u"Data handler %s not available") % self.dbtype)

        self.ensureWikiTempDir()
        wikiData = wikiDataFactory(self, self.dataDir, self.getWikiTempDir())

        self.baseWikiData = wikiData
        self.wikiData = WikiDataSynchronizedProxy(self.baseWikiData)
        
        self.wikiData.connect()
        
        # Reset flag so program automatically tries reconnecting on next error
        self.autoReconnectTriedFlag = False

        props = {"reconnected database": True,}
        self.fireMiscEventProps(props)




    def miscEventHappened(self, miscevt):
        """
        Handle misc events from DocPages
        """
        if miscevt.getSource() is self.wikiConfiguration:
            if miscevt.has_key("changed configuration"):
                self.getFormatting().rebuildFormatting(miscevt)
                props = miscevt.getProps().copy()
                props["changed wiki configuration"] = True
                self.fireMiscEventProps(props)                
        elif miscevt.getSource() is GetApp():
            if miscevt.has_key("reread cc blacklist needed"):
                self._updateCcWordBlacklist()
        else:
            # These messages come from (classes derived from) DocPages,
            # they are mainly relayed

            if miscevt.has_key_in(("deleted wiki page", "renamed wiki page")):
                self.autoLinkRelaxRE = None
                props = miscevt.getProps().copy()
                props["wikiPage"] = miscevt.getSource()
                self.fireMiscEventProps(props)
            elif miscevt.has_key("updated wiki page"):            
                props = miscevt.getProps().copy()
                props["wikiPage"] = miscevt.getSource()
                self.fireMiscEventProps(props)
            elif miscevt.has_key("saving new wiki page"):            
                self.autoLinkRelaxRE = None
            elif miscevt.has_key("reread cc blacklist needed"):
                self._updateCcWordBlacklist()
                
                props = miscevt.getProps().copy()
                props["funcPage"] = miscevt.getSource()
                self.fireMiscEventProps(props)
            elif miscevt.has_key("updated func page"):
                # This was send from a FuncPage object, send it again
                # The event also contains more specific information
                # handled by PersonalWikiFrame
                props = miscevt.getProps().copy()
                props["funcPage"] = miscevt.getSource()

                self.fireMiscEventProps(props)



