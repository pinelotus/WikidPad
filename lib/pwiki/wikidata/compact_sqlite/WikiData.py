"""

Used terms:
    
    wikiword -- a string matching one of the wiki word regexes
    wiki page -- real existing content stored and associated with a wikiword
            (which is the page name). Sometimes page is synonymous for page name
    alias -- wikiword without content but associated to a page name.
            For the user it looks as if the content of the alias is the content
            of the page for the associated page name
    defined wiki word -- either a page name or an alias
"""



from os import mkdir, unlink, listdir, rename, stat, utime
from os.path import exists, join, basename
from time import time, localtime
import datetime
import string, glob, types, sets, traceback
import pwiki.srePersistent as re

from pwiki.WikiExceptions import *   # TODO make normal import
from pwiki import SearchAndReplace

try:
    import pwiki.sqlite3api as sqlite
    import DbStructure
    from DbStructure import createWikiDB, WikiDBExistsException
except:
    sqlite = None
# finally:
#     pass

from pwiki.StringOps import getBinCompactForDiff, applyBinCompact, pathEnc, pathDec,\
        binCompactToCompact, fileContentToUnicode, utf8Enc, utf8Dec, Tokenizer, \
        uniWithNone, loadEntireTxtFile, Conjunction

from pwiki import WikiFormatting
from pwiki import PageAst

# from pwiki.DocPages import WikiPage


class WikiData:
    "Interface to wiki data."
    def __init__(self, wikiDocument, dataDir, tempDir):
        self.wikiDocument = wikiDocument
        self.dataDir = dataDir
        self.cachedContentNames = None
#         tempDir = uniWithNone(tempDir)

        dbfile = join(dataDir, "wiki.sli")

        try:
            if (not exists(pathEnc(dbfile))):
                DbStructure.createWikiDB(None, dataDir)  # , True
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)

        dbfile = pathDec(dbfile)
        try:
            self.connWrap = DbStructure.ConnectWrapSyncCommit(
                    sqlite.connect(dbfile))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)
        
#         self.connWrap.execSql("pragma temp_store_directory = '%s'" %
#                 utf8Enc(tempDir)[0])

        DbStructure.registerSqliteFunctions(self.connWrap)


    def checkDatabaseFormat(self):
        return DbStructure.checkDatabaseFormat(self.connWrap)


    def connect(self):
        formatcheck, formatmsg = self.checkDatabaseFormat()

        if formatcheck == 2:
            # Unknown format
            raise WikiDataException, formatmsg

        # Update database from previous versions if necessary
        if formatcheck == 1:
            try:
                DbStructure.updateDatabase(self.connWrap)
            except:
                self.connWrap.rollback()
                raise

        lastException = None
        try:
            # Further possible updates
            DbStructure.updateDatabase2(self.connWrap)
        except sqlite.Error, e:
            # Remember but continue
            lastException = DbWriteAccessError(e)

        # Activate UTF8 support for text in database (content is blob!)
        DbStructure.registerUtf8Support(self.connWrap)

        # Function to convert from content in database to
        # return value, used by getContent()
        self.contentDbToOutput = lambda c: utf8Dec(c, "replace")[0]
        
        try:
            # Set marker for database type
            self.wikiDocument.getWikiConfig().set("main", "wiki_database_type",
                    "compact_sqlite")
        except (IOError, OSError), e:
            # Remember but continue
            lastException = DbWriteAccessError(e)

        # Function to convert unicode strings from input to content in database
        # used by setContent

        def contentUniInputToDb(unidata):
            return utf8Enc(unidata, "replace")[0]

        self.contentUniInputToDb = contentUniInputToDb
        
        try:
#             self.connWrap.execSql("pragma synchronous = 0")

            self._createTempTables()

            # reset cache
            self.cachedContentNames = None

#             # cache aliases
#             aliases = self.getAllAliases()
#             for alias in aliases:
#                 self.cachedContentNames[alias] = 2
#     
#             # Cache real words
#             for word in self.getAllDefinedContentNames():
#                 self.cachedContentNames[word] = 1
    
            self.cachedGlobalProps = None
            self.getGlobalProperties()
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            try:
                self.connWrap.rollback()
            except (IOError, OSError, sqlite.Error), e2:
                traceback.print_exc()
                raise DbReadAccessError(e2)
            raise DbReadAccessError(e)
            
        if lastException:
            raise lastException


    def _reinit(self):
        """
        Actual initialization or reinitialization after rebuildWiki()
        """
        
    def _createTempTables(self):
        # Temporary table for findBestPathFromWordToWord
        # TODO: Possible for read-only dbs?

        # These schema changes are only on a temporary table so they are not
        # in DbStructure.py
        self.connWrap.execSql("create temp table temppathfindparents "
                "(word text primary key, child text, steps integer)")

        self.connWrap.execSql("create index temppathfindparents_steps "
                "on temppathfindparents(steps)")


    # ---------- Direct handling of page data ----------
    
    def getContent(self, word):
        try:
            result = self.connWrap.execSqlQuerySingleItem("select content from "+\
                "wikiwordcontent where word = ?", (word,), None)

            if result is None:
                raise WikiFileNotFoundException, "wiki page not found: %s" % word
    
            return self.contentDbToOutput(result)
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def _getContentAndInfo(self, word):
        """
        Get content and further information about a word
        
        Not part of public API!
        """
        try:
            result = self.connWrap.execSqlQuery("select content, modified from "+\
                "wikiwordcontent where word = ?", (word,))
            if len(result) == 0:
                raise WikiFileNotFoundException, "wiki page not found: %s" % word
    
            content = self.contentDbToOutput(result[0][0])
            return (content, result[0][1])
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def setContent(self, word, content, moddate = None, creadate = None):
        """
        Sets the content, does not modify the cache information
        except self.cachedContentNames
        """
        if not content: content = u""  # ?
        
        assert type(content) is unicode

        content = self.contentUniInputToDb(content)
        self.setContentRaw(word, content, moddate, creadate)

        self._getCachedContentNames()[word] = 1


    def setContentRaw(self, word, content, moddate = None, creadate = None):
        """
        Sets the content without applying any encoding, used by versioning,
        does not modify the cache information
        
        moddate -- Modification date to store or None for current
        creadate -- Creation date to store or None for current 
        
        Not part of public API!
        """
        ti = time()
        if moddate is None:
            moddate = ti

        # if not content: content = ""
        
        assert type(content) is str

        try:
            if self.connWrap.execSqlQuerySingleItem("select word from "+\
                    "wikiwordcontent where word=?", (word,), None) is not None:
    
                # Word exists already
    #             self.connWrap.execSql("insert or replace into wikiwordcontent"+\
    #                 "(word, content, modified) values (?,?,?)",
    #                 (word, sqlite.Binary(content), moddate))
                self.connWrap.execSql("update wikiwordcontent set "
                    "content=?, modified=? where word=?",
                    (sqlite.Binary(content), moddate, word))
            else:
                if creadate is None:
                    creadate = ti
    
                # Word does not exist -> record creation date
                self.connWrap.execSql("insert or replace into wikiwordcontent"
                    "(word, content, modified, created, wordnormcase) "
                    "values (?,?,?,?,?)",
                    (word, sqlite.Binary(content), moddate, creadate, word.lower()))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    def renameContent(self, oldWord, newWord):
        """
        The content which was stored under oldWord is stored
        after the call under newWord. The self.cachedContentNames
        dictionary is updated, other caches won't be updated.
        """
        try:
            self.connWrap.execSql("update wikiwordcontent set word = ?, wordnormcase = ? "
                    "where word = ?", (newWord, newWord.lower(), oldWord))
    
            del self._getCachedContentNames()[oldWord]
            self._getCachedContentNames()[newWord] = 1
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    def deleteContent(self, word):
        """
        Deletes a page
        """
        try:
            self.connWrap.execSql("delete from wikiwordcontent where word = ?", (word,))
            del self._getCachedContentNames()[word]
        except sqlite.Error:  # TODO !!!
            raise WikiFileNotFoundException, "wiki page for deletion not found: %s" % word

    def getTimestamps(self, word):
        """
        Returns a tuple with modification, creation and visit date of
        a word or (None, None, None) if word is not in the database
        """
        try:
            dates = self.connWrap.execSqlQuery(
                    "select modified, created from wikiwordcontent where word = ?",
                    (word,))

            if len(dates) > 0:
                return (float(dates[0][0]), float(dates[0][1]), 0.0)
            else:
                return (None, None, None)  # ?
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def setTimestamps(self, word, timestamps):
        """
        Set timestamps for an existing wiki page.
        """
        moddate, creadate = timestamps[:2]

        try:
            data = self.connWrap.execSqlQuery("select word from wikiwordcontent "
                    "where word = ?", (word,))
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        try:
            if len(data) < 1:
                raise WikiFileNotFoundException
            else:
                self.connWrap.execSql("update wikiwordcontent set modified = ?, "
                        "created = ? where word = ?", (moddate, creadate, word))
        except (IOError, OSError, ValueError), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    def getExistingWikiWordInfo(self, wikiWord, withFields=()):
        """
        Get information about an existing wiki word
        Aliases must be resolved beforehand.
        Function must work for read-only wiki.
        withFields -- Seq. of names of fields which should be included in
            the output. If this is not empty, a tuple is returned
            (relation, ...) with ... as further fields in the order mentioned
            in withfields.

            Possible field names:
                "modified": Modification date of page
                "created": Creation date of page
                "visited": Last visit date of page (currently always returns 0)
                "firstcharpos": Dummy returning very high value
        """
        if withFields is None:
            withFields = ()

        addFields = ""
        converters = [lambda s: s]

        for field in withFields:
            if field == "modified":
                addFields += ", modified"
                converters.append(float)
            elif field == "created":
                addFields += ", created"
                converters.append(float)
            elif field == "visited":
                # Fake "visited" field
                addFields += ", 0.0"
                converters.append(lambda s: 0.0)
            elif field == "firstcharpos":
                # Fake character position. TODO More elegantly
                addFields += ", 0"
                converters.append(lambda s: 2000000000L)


        sql = "select word%s from wikiwordcontent where word = ?" % addFields

        try:
            if len(withFields) > 0:
                dbresult = [tuple(c(item) for c, item in zip(converters, row))
                        for row in self.connWrap.execSqlQuery(sql, (wikiWord,))]
            else:
                dbresult = self.connWrap.execSqlQuerySingleColumn(sql, (wikiWord,))
            
            if len(dbresult) == 0:
                raise WikiWordNotFoundException(wikiWord)
            
            return dbresult[0]
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    # ---------- Renaming/deleting pages with cache update ----------

    def renameWord(self, word, toWord):
        if not self.wikiDocument.getFormatting().isNakedWikiWord(toWord):
            raise WikiDataException(_(u"'%s' is an invalid wiki word") % toWord)

        if self.isDefinedWikiWord(toWord):
            raise WikiDataException(
                    _(u"Cannot rename '%s' to '%s', '%s' already exists") %
                    (word, toWord, toWord))

        try:
            # commit anything pending so we can rollback on error
            self.connWrap.syncCommit()
    
            try:
                self.connWrap.execSql("update wikirelations set word = ? where word = ?", (toWord, word))
                self.connWrap.execSql("update wikiwordprops set word = ? where word = ?", (toWord, word))
                self.connWrap.execSql("update todos set word = ? where word = ?", (toWord, word))
                self.renameContent(word, toWord)
    
                self.connWrap.commit()
            except:
                self.connWrap.rollback()
                raise
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    def deleteWord(self, word):
        """
        delete everything about the wikiword passed in. an exception is raised
        if you try and delete the wiki root node.
        """
        if word != self.wikiDocument.getWikiName():
            try:
                self.connWrap.syncCommit()
                try:
                    # don't delete the relations to the word since other
                    # pages still have valid outward links to this page.
                    # just delete the content
    
                    self.connWrap.execSql("delete from wikirelations where word = ?", (word,))
                    self.connWrap.execSql("delete from wikiwordprops where word = ?", (word,))
                    self.connWrap.execSql("delete from todos where word = ?", (word,))
                    self.deleteContent(word)
                    # self.connWrap.execSql("delete from wikiwordcontent where word = ?", (word,))
                    # del self.cachedContentNames[word]
    
                    self.connWrap.commit()
    
                    # due to some bug we have to close and reopen the db sometimes (gadfly)
                    ## self.dbConn.close()
                    ## self.dbConn = gadfly.gadfly("wikidb", self.dataDir)
    
                except:
                    self.connWrap.rollback()
                    raise
            except (IOError, OSError, sqlite.Error), e:
                traceback.print_exc()
                raise DbWriteAccessError(e)
        else:
            raise WikiDataException(_(u"You cannot delete the root wiki node"))


    # ---------- Handling of relationships cache ----------

#     def getAllRelations(self):
#         "get all of the relations in the db"
#         relations = []
#         data = self.connWrap.execSqlQuery("select word, relation from wikirelations")
#         for row in data:
#             relations.append((row[0], row[1]))
#         return relations


    def getChildRelationships(self, wikiWord, existingonly=False,
            selfreference=True, withFields=()):
        """
        get the child relations of this word
        Function must work for read-only wiki.
        existingonly -- List only existing wiki words
        selfreference -- List also wikiWord if it references itself
        withFields -- Seq. of names of fields which should be included in
            the output. If this is not empty, tuples are returned
            (relation, ...) with ... as further fields in the order mentioned
            in withfields.

            Possible field names:
                "firstcharpos": position of link in page (may be -1 to represent
                    unknown)
                "modified": Modification date
        """
        if withFields is None:
            withFields = ()

        addFields = ""
        converters = [lambda s: s]
        for field in withFields:
            if field == "firstcharpos":
                addFields += ", firstcharpos"
                converters.append(lambda s: s)
            elif field == "modified":
                # "modified" isn't a field of wikirelations. We need
                # some SQL magic to retrieve the modification date
                addFields += (", ifnull((select modified from wikiwordcontent "
                        "where wikiwordcontent.word = relation or "
                        "wikiwordcontent.word = (select word from wikiwordprops "
                        "where key = 'alias' and value = relation)), 0.0)")
                converters.append(float)

        
        sql = "select relation%s from wikirelations where word = ?" % addFields

        if existingonly:
            # filter to only words in wikiwords or aliases
            sql += " and (exists (select word from wikiwordcontent "+\
                    "where word = relation) or exists "+\
                    "(select value from wikiwordprops "+\
                    "where value = relation and key = 'alias'))"

        if not selfreference:
            sql += " and relation != word"
            
        try:
            if len(withFields) > 0:
                return self.connWrap.execSqlQuery(sql, (wikiWord,))
            else:
                return self.connWrap.execSqlQuerySingleColumn(sql, (wikiWord,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)




#     def getChildRelationshipsAndHasChildren(self, wikiWord, existingonly=False,
#             selfreference=True):
#         """
#         get the child relations to this word as sequence of tuples
#             (<child word>, <has child children?>). Used when expanding
#             a node in the tree control. If cycles are forbidden in the
#             tree, a True in the "children" flag must be checked
#             for cycles, a False is always correct.
# 
#         existingonly -- List only existing wiki words
#         selfreference -- List also wikiWord if it references itself
#         """
#         innersql = "select relation from wikirelations as innerrel where "+\
#                 "word = wikirelations.relation"
#         if existingonly:
#             # filter to only words in wikiwordcontent or aliases
#             innersql += " and (exists (select word from wikiwordcontent "+\
#                     "where word = relation) or exists "+\
#                     "(select value from wikiwordprops "+\
#                     "where value = relation and key = 'alias'))"
# 
#         if not selfreference:
#             innersql += " and relation != word"
# 
# 
#         outersql = "select relation, exists(%s) from wikirelations where word = ?"
#         if existingonly:
#             # filter to only words in wikiwordcontent or aliases
#             outersql += " and (exists (select word from wikiwordcontent "+\
#                     "where word = relation) or exists "+\
#                     "(select value from wikiwordprops "+\
#                     "where value = relation and key = 'alias'))"
# 
#         if not selfreference:
#             outersql += " and relation != word"
# 
#         outersql = outersql % innersql
# 
# 
#         return self.connWrap.execSqlQuery(outersql, (wikiWord,))


    def getParentRelationships(self, wikiWord):
        "get the parent relations to this word"
#         return self.connWrap.execSqlQuerySingleColumn(
#                 "select word from wikirelations where relation = ?", (wikiWord,))
 
        # Parents of the real word
        wikiWord = self.getAliasesWikiWord(wikiWord)
        try:
            parents = sets.Set(self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikirelations where relation = ?", (wikiWord,)))
    
            # Plus parents of aliases
            aliases = [v for k, v in self.getPropertiesForWord(wikiWord)
                    if k == u"alias"]
    
            for al in aliases:
                parents.union_update(self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikirelations where relation = ?", (al,)))
    
            return list(parents)
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getParentlessWikiWords(self):
        """
        get the words that have no parents.
        """
        try:
#             return self.connWrap.execSqlQuerySingleColumn(
#                     "select word from wikiwordcontent where not word glob '[[]*' "
#                     "except select relation from wikirelations "
#                     "except select word from wikiwordprops where key='alias' and "
#                     "value in (select relation from wikirelations)")

            return self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikiwordcontent where not word glob '[[]*' "
                    "except select "
                    "ifnull(wikiwordprops.word, wikirelations.relation) as unaliased "
                    "from wikirelations left join wikiwordprops "
                    "on wikirelations.relation = wikiwordprops.value and "
                    "wikiwordprops.key = 'alias' where unaliased != wikirelations.word")

        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getUndefinedWords(self):
        """
        List words which are childs of a word but are not defined, neither
        directly nor as alias.
        """
        try:
            return self.connWrap.execSqlQuerySingleColumn(
                    "select relation from wikirelations "
                    "except select word from wikiwordcontent where not word glob '[[]*' "
                    "except select value from wikiwordprops where key='alias'")
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def addRelationship(self, word, rel):
        """
        Add a relationship from word to rel. rel is a tuple (toWord, pos).
        A relation from one word to another is unique and can't be added twice.
        """
        try:
#             self.connWrap.execSql(
#                     "insert or replace into wikirelations(word, relation) "
#                     "values (?, ?)", (word, toWord))
            self.connWrap.execSql(
                    "insert or replace into wikirelations(word, relation, firstcharpos) "
                    "values (?, ?, ?)", (word, rel[0], rel[1]))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)

    def updateChildRelations(self, word, childRelations):
        self.deleteChildRelationships(word)
        for r in childRelations:
            self.addRelationship(word, r)

    def deleteChildRelationships(self, fromWord):
        try:
            self.connWrap.execSql(
                    "delete from wikirelations where word = ?", (fromWord,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    # TODO Maybe optimize
    def getAllSubWords(self, words, level=-1):
        """
        Return all words which are children, grandchildren, etc.
        of words and the words itself. Used by the "export/print Sub-Tree"
        functions. All returned words are real existing words, no aliases.
        """
        checkList = [(self.getAliasesWikiWord(w), 0) for w in words]
        checkList.reverse()
        
        resultSet = {}
        result = []

        while len(checkList) > 0:
            toCheck, chLevel = checkList.pop()
            if resultSet.has_key(toCheck):
                continue

            result.append(toCheck)
            resultSet[toCheck] = None
            
            if level > -1 and chLevel >= level:
                continue  # Don't go deeper
            
            children = self.getChildRelationships(toCheck, existingonly=True,
                    selfreference=False)
                    
            children = [(self.getAliasesWikiWord(c), chLevel + 1)
                    for c in children]
            children.reverse()
            checkList += children

        return result


    def findBestPathFromWordToWord(self, word, toWord):
        """
        finds the shortest path from word to toWord going through the parents.
        word and toWord are included as first/last element. If word == toWord,
        it is included only once as the single element of the list.
        If there is no path from word to toWord, [] is returned
        """

        if word == toWord:
            return [word]

        try:
            # Clear temporary table
            self.connWrap.execSql("delete from temppathfindparents")
    
            self.connWrap.execSql("insert into temppathfindparents "+
                    "(word, child, steps) select word, relation, 1 from wikirelations "+
                    "where relation = ?", (word,))
    
            step = 1
            while True:
                changes = self.connWrap.rowcount
    
                if changes == 0:
                    # No more (grand-)parents
                    return []
    
                if self.connWrap.execSqlQuerySingleItem("select word from "+
                        "temppathfindparents where word=?", (toWord,)) is not None:
                    # Path found
                    result = [toWord]
                    crumb = toWord
    
                    while crumb != word:
                        crumb = self.connWrap.execSqlQuerySingleItem(
                                "select child from temppathfindparents where "+
                                "word=?", (crumb,))
                        result.append(crumb)
    
                    # print "findBestPathFromWordToWord result", word, toWord, repr(result)
    
                    # Clear temporary table
                    self.connWrap.execSql("delete from temppathfindparents")
    
                    return result
    
                self.connWrap.execSql("""
                    insert or ignore into temppathfindparents (word, child, steps)
                    select wikirelations.word, temppathfindparents.word, ? from
                        temppathfindparents inner join wikirelations on
                        temppathfindparents.word == wikirelations.relation where
                        temppathfindparents.steps == ?
                    """, (step+1, step))
    
                step += 1
        
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    # ---------- Listing/Searching wiki words (see also "alias handling", "searching pages")----------

    def getAllDefinedWikiPageNames(self):
        "get the names of all wiki pages in the db, no aliases"
        try:
            return self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikiwordcontent where not word glob '[[]*'")
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getAllDefinedContentNames(self):
        """
        Get the names of all the content elements in the db, no aliases.
        Content elements are wiki pages plus functional pages and possible
        other data, their names begin with '['
        """
        try:
            return self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikiwordcontent")
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def refreshDefinedContentNames(self):
        """
        Refreshes the internal list of defined pages which
        may be different from the list of pages for which
        content is available (not possible for compact database).
        The function tries to conserve additional informations
        (creation/modif. date) if possible.
        
        It is mainly called during rebuilding of the wiki 
        so it must not rely on the presence of other cache
        information (e.g. relations).
        
        The self.cachedContentNames is invalidated.
        """
        self.cachedContentNames = None

#         # cache aliases
#         aliases = self.getAllAliases()
#         for alias in aliases:
#             self.cachedContentNames[alias] = 2
# 
#         # recreate word caches
#         for word in self.getAllDefinedContentNames():
#             self.cachedContentNames[word] = 1


    def _getCachedContentNames(self):
        try:
            if self.cachedContentNames is None:
                result = {}
        
                # cache aliases
                aliases = self.getAllAliases()
                for alias in aliases:
                    result[alias] = 2
        
                # Cache real words
                for word in self.getAllDefinedContentNames():
                    result[word] = 1
                    
                self.cachedContentNames = result
    
            return self.cachedContentNames
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


#     # TODO More general Wikiword to filename mapping
#     def getAllPageNamesFromDisk(self):   # Used for rebuilding wiki
#         return self.connWrap.execSqlQuerySingleColumn("select word from wikiwordcontent")

#     # TODO More general Wikiword to filename mapping
#     def getWikiWordFileName(self, wikiWord):
#         return join(self.dataDir, u"%s.wiki" % wikiWord)

    def isDefinedWikiWord(self, word):
        "check if a word is a valid wikiword (page name or alias)"
        return self._getCachedContentNames().has_key(word)

    def getWikiWordsStartingWith(self, thisStr, includeAliases=False,
            caseNormed=False):
        "get the list of words starting with thisStr. used for autocompletion."

        # Escape some characters:   # TODO more elegant
        thisStr = thisStr.replace("[", "[[").replace("]", "[]]").replace("[[", "[[]")
        if caseNormed:
            thisStr = thisStr.lower()   # TODO More general normcase function
            if includeAliases:
                return self.connWrap.execSqlQuerySingleColumn(
                        "select word from wikiwordcontent where wordnormcase glob (? || '*') union "
                        "select utf8Normcase(value) from wikiwordprops where key = 'alias' and value glob (? || '*')",
                        (thisStr, thisStr))
            else:
                return self.connWrap.execSqlQuerySingleColumn(
                        "select word from wikiwordcontent where wordnormcase glob (? || '*')",
                        (thisStr,))
        else:
            if includeAliases:
                return self.connWrap.execSqlQuerySingleColumn(
                        "select word from wikiwordcontent where word glob (? || '*') union "
                        "select value from wikiwordprops where key = 'alias' and value glob (? || '*')",
                        (thisStr, thisStr))
            else:
                return self.connWrap.execSqlQuerySingleColumn(
                        "select word from wikiwordcontent where word glob (? || '*')",
                        (thisStr,))


    def getWikiWordsWith(self, thisStr, includeAliases=False):
        """
        get the list of words with thisStr in them,
        if possible first these which start with thisStr.
        """
        thisStr = thisStr.lower()   # TODO More general normcase function

        try:
            result1 = self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikiwordcontent where wordnormcase like (? || '%')",
                    (thisStr,))
    
            if includeAliases:
                result1 += self.connWrap.execSqlQuerySingleColumn(
                        "select value from wikiwordprops where key = 'alias' and "
                        "utf8Normcase(value) like (? || '%')", (thisStr,))
    
            result2 = self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikiwordcontent "
                    "where wordnormcase like ('%' || ? || '%') and "
                    "wordnormcase not like (? || '%') and word not glob '[[]*'",
                    (thisStr, thisStr))
    
            if includeAliases:
                result2 += self.connWrap.execSqlQuerySingleColumn(
                        "select value from wikiwordprops where key = 'alias' and "
                        "utf8Normcase(value) like ('%' || ? || '%') and "
                        "utf8Normcase(value) not like (? || '%')",
                        (thisStr, thisStr))
    
            coll = self.wikiDocument.getCollator()
            
            coll.sort(result1)
            coll.sort(result2)

            return result1 + result2
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


#     def getWikiWordsModifiedLastDays(self, days):
#         timeDiff = float(time()-(86400*days))
#         try:
#             return self.connWrap.execSqlQuerySingleColumn(
#                     "select word from wikiwordcontent where modified >= ? and "
#                     "not word glob '[[]*'",
#                     (timeDiff,))
#         except (IOError, OSError, sqlite.Error), e:
#             traceback.print_exc()
#             raise DbReadAccessError(e)


    def getWikiWordsModifiedWithin(self, startTime, endTime):
        """
        Function must work for read-only wiki.
        startTime and endTime are floating values as returned by time.time()
        startTime is inclusive, endTime is exclusive
        """
        try:
            return self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikiwordcontent where modified >= ? and "
                    "modified < ? and not word glob '[[]*'",
                    (startTime, endTime))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    _STAMP_TYPE_TO_FIELD = {
            0: "modified",
            1: "created"
        }

    def getTimeMinMax(self, stampType):
        """
        Return the minimal and maximal timestamp values over all wiki words
        as tuple (minT, maxT) of float time values.
        A time value of 0.0 is not taken into account.
        If there are no wikiwords with time value != 0.0, (None, None) is
        returned.
        
        stampType -- 0: Modification time, 1: Creation, 2: Last visit
        """
        field = self._STAMP_TYPE_TO_FIELD.get(stampType)
        if field is None:
            # Visited not supported yet
            return (None, None)

        try:
            result = self.connWrap.execSqlQuery(
                    ("select min(%s), max(%s) from wikiwordcontent where %s > 0 and "
                    "not word glob '[[]*'") % (field, field, field))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        if len(result) == 0:
            # No matching wiki words found
            return (None, None)
        else:
            return tuple(result[0])  # return (float(result[0][0]), float(result[0][1]))



    def getWikiWordsBefore(self, stampType, stamp, limit=None):
        """
        Get a list of tuples of wiki words and dates related to a particular
        time before stamp.
        
        stampType -- 0: Modification time, 1: Creation, 2: Last visit
        limit -- How much words to return or None for all
        """
        field = self._STAMP_TYPE_TO_FIELD.get(stampType)
        if field is None:
            # Visited not supported yet
            return []
            
        if limit is None:
            limit = -1
            
        try:
            return self.connWrap.execSqlQuery(
                    ("select word, %s from wikiwordcontent where %s > 0 and %s < ? "
                    "and not word glob '[[]*' order by %s desc limit ?") %
                    (field, field, field, field), (stamp, limit))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getWikiWordsAfter(self, stampType, stamp, limit=None):
        """
        Get a list of of tuples of wiki words and dates related to a particular
        time after OR AT stamp.
        
        stampType -- 0: Modification time, 1: Creation, 2: Last visit
        limit -- How much words to return or None for all
        """
        field = self._STAMP_TYPE_TO_FIELD.get(stampType)
        if field is None:
            # Visited not supported yet
            return []
            
        if limit is None:
            limit = -1

        try:
            return self.connWrap.execSqlQuery(
                    ("select word, %s from wikiwordcontent where %s > ? "
                    "and not word glob '[[]*' order by %s asc limit ?") %
                    (field, field, field), (stamp, limit))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getFirstWikiWord(self):
        """
        Returns the name of the "first" wiki word. See getNextWikiWord()
        for details. Returns either an existing wiki word or None if no
        wiki words in database.
        """
        try:
            return self.connWrap.execSqlQuerySingleItem(
                    "select word from wikiwordcontent where not word glob '[[]*' "
                    "order by word limit 1")
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getNextWikiWord(self, currWord):
        """
        Returns the "next" wiki word after currWord or None if no
        next word exists. If you begin with the first word returned
        by getFirstWikiWord() and then use getNextWikiWord() to
        go to the next word until no more words are available
        and if the list of existing wiki words is not modified during
        iteration, it is guaranteed that you have visited all real
        wiki words (no aliases) then.
        """
        try:
            return self.connWrap.execSqlQuerySingleItem(
                    "select word from wikiwordcontent where not word glob '[[]*' and "
                    "word > ? order by word limit 1", (currWord,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)



    # ---------- Property cache handling ----------

    def getPropertyNames(self):
        """
        Return all property names not beginning with "global."
        """
        try:
            return self.connWrap.execSqlQuerySingleColumn(
                    "select distinct(key) from wikiwordprops "
                    "where key not glob 'global.*'")
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    # TODO More efficient? (used by autocompletion)
    def getPropertyNamesStartingWith(self, startingWith):
        try:
            names = self.connWrap.execSqlQuerySingleColumn(
                    "select distinct(key) from wikiwordprops")   #  order by key")
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        return [name for name in names if name.startswith(startingWith)]

    def getGlobalProperties(self):
        if not self.cachedGlobalProps:
            return self.updateCachedGlobalProps()

        return self.cachedGlobalProps

    def getDistinctPropertyValues(self, key):
        try:
            return self.connWrap.execSqlQuerySingleColumn(
                    "select distinct(value) from wikiwordprops where key = ? "
                    # "order by value", (key,))
                    , (key,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getPropertyTriples(self, word, key, value):
        """
        Function must work for read-only wiki.
        word, key and value can either be unistrings or None.
        """
        
        conjunction = Conjunction("where ", "and ")

        query = "select word, key, value from wikiwordprops "
        parameters = []
        
        if word is not None:
            parameters.append(word)
            query += conjunction() + "word = ? "
        
        if key is not None:
            parameters.append(key)
            query += conjunction() + "key = ? "

        if value is not None:
            parameters.append(value)
            query += conjunction() + "value = ? "

        try:
            return self.connWrap.execSqlQuery(query, tuple(parameters))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getWordsForPropertyName(self, key):
        try:
            return self.connWrap.execSqlQuerySingleColumn(
                    "select distinct(word) from wikiwordprops where key = ? ",
                    (key,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getWordsWithPropertyValue(self, key, value):
        try:
            return self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikiwordprops where key = ? and value = ?",
                    (key, value))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def getPropertiesForWord(self, word):
        """
        Returns list of tuples (key, value) of key and value
        of all properties for word.
        """
        try:
            return self.connWrap.execSqlQuery("select key, value "+
                        "from wikiwordprops where word = ?", (word,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def setProperty(self, word, key, value):
        try:
            self.connWrap.execSql("insert into wikiwordprops(word, key, value) "
                    "values (?, ?, ?)", (word, key, value))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    def updateProperties(self, word, props):
        self.deleteProperties(word)
        for k in props.keys():
            values = props[k]
            for v in values:
                self.setProperty(word, k, v)
                if k == "alias":
                    self.setAsAlias(v)  # TODO

        self.cachedGlobalProps = None   # reset global properties cache


    def updateCachedGlobalProps(self):
        """
        TODO: Should become part of public API!
        """
        try:
            data = self.connWrap.execSqlQuery("select key, value from wikiwordprops "
                    "where key glob 'global.*'")
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

        globalMap = {}
        for (key, val) in data:
            globalMap[key] = val

        self.cachedGlobalProps = globalMap

        return globalMap


    def deleteProperties(self, word):
        try:
            self.connWrap.execSql("delete from wikiwordprops where word = ?",
                    (word,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    # ---------- Alias handling ----------

    def getAliasesWikiWord(self, alias):
        """
        If alias is an alias wiki word, return the original word,
        otherwise return alias
        """
        if not self.isAlias(alias):
            return alias

        aliases = self.getWordsWithPropertyValue("alias", alias)
        if len(aliases) > 0:
            return aliases[0]
        return alias  # None

    def isAlias(self, word):
        "check if a word is an alias for another"
        return self._getCachedContentNames().get(word) == 2
        
    def setAsAlias(self, word):
        """
        Sets this word in internal cache to be an alias
        """
        if self._getCachedContentNames().get(word, 2) == 2:
            self._getCachedContentNames()[word] = 2

        
    def getAllAliases(self):
        try:
            # get all of the aliases
            return self.connWrap.execSqlQuerySingleColumn(
                    "select value from wikiwordprops where key = 'alias'")
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)



    # ---------- Todo cache handling ----------

    def getTodos(self):
        try:
            return self.connWrap.execSqlQuery("select word, todo from todos")
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

    def getTodosForWord(self, word):
        """
        Returns list of all todo items of word
        """
        try:
            return self.connWrap.execSqlQuerySingleColumn("select todo from todos "
                    "where word = ?", (word,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def updateTodos(self, word, todos):
        self.deleteTodos(word)
        for t in todos:
            self.addTodo(word, t)


    def addTodo(self, word, todo):
        try:
            self.connWrap.execSql("insert into todos(word, todo) values (?, ?)", (word, todo))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    def deleteTodos(self, word):
        try:
            self.connWrap.execSql("delete from todos where word = ?", (word,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    # ---------- Searching pages ----------

    # TODO Other searchmodes
    def search_fallback(self, forPattern, processAnds=True, caseSensitive=False,
            searchmode=0):
        """
        Backup method for non sqlite (without user-defined functions).
        Currently unused
        """
        if caseSensitive:
            reFlags = re.MULTILINE | re.UNICODE
        else:
            reFlags = re.IGNORECASE | re.MULTILINE | re.UNICODE
        
        if processAnds:
            andPatterns = [re.compile(pattern, reFlags)
                           for pattern in forPattern.split(u' and ')]
#                            for pattern in forPattern.lower().split(u' and ')]
        else:
            andPatterns = [re.compile(forPattern, reFlags)]


        # execSqlQueryIter is insecure, don't use
        itr = self.connWrap.execSqlQueryIter(
                "select word, content from wikiwordcontent")

        results = []

        for word, content in itr:
            for pattern in andPatterns:
                if not pattern.search(content):
                    word = None
                    break

            if word:
                results.append(word)

        return results


#     # TODO Other searchmodes
#     def search_old(self, forPattern, processAnds=True, caseSensitive=False,
#             searchmode=0):
#         """
#         Search all content for the forPattern.
#         This version uses sqlite user-defined functions.
#         Use search_fallback for other databases
#         """
#         if caseSensitive:
#             reFlags = re.MULTILINE | re.UNICODE
#         else:
#             reFlags = re.IGNORECASE | re.MULTILINE | re.UNICODE
#         
#         if processAnds:
#             andPatterns = [re.compile(pattern, reFlags)
#                            for pattern in forPattern.split(u' and ')]
# #                            for pattern in forPattern.lower().split(u' and ')]
#         else:
#             andPatterns = [re.compile(forPattern, reFlags)]
# 
#         result = self.connWrap.execSqlQuerySingleColumn(
#                 "select word from wikiwordcontent where "+\
#                 "testMatch(content, ?)", (sqlite.addTransObject(andPatterns),))
# 
#         sqlite.delTransObject(andPatterns)
# 
#         return result


    def search(self, sarOp, exclusionSet):
        """
        Search all wiki pages using the SearchAndReplaceOperation sarOp and
        return set of all page names that match the search criteria.
        sarOp.beginWikiSearch() must be called before calling this function,
        sarOp.endWikiSearch() must be called after calling this function.
        This version uses sqlite user-defined functions.
        
        exclusionSet -- set of wiki words for which their pages shouldn't be
        searched here and which must not be part of the result set
        """
        try:
            result = self.connWrap.execSqlQuerySingleColumn(
                    "select word from wikiwordcontent where "+\
                    "word not glob '[[]*' and testMatch(word, content, ?)",
                    (sqlite.addTransObject(sarOp),))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            sqlite.delTransObject(sarOp)
            raise DbReadAccessError(e)

        sqlite.delTransObject(sarOp)
        
        result = sets.Set(result)
        result -= exclusionSet

        return result


    def saveSearch(self, title, datablock):
        "save a search into the search_views table"
        try:
            self.connWrap.execSql(
                    "insert or replace into search_views(title, datablock) "+\
                    "values (?, ?)", (title, sqlite.Binary(datablock)))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)

    def getSavedSearchTitles(self):
        """
        Return the titles of all stored searches in alphabetical order
        """
        try:
            return self.connWrap.execSqlQuerySingleColumn(
                    "select title from search_views order by title")
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

    def getSearchDatablock(self, title):
        try:
            return self.connWrap.execSqlQuerySingleItem(
                    "select datablock from search_views where title = ?", (title,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)

    def deleteSavedSearch(self, title):
        try:
            self.connWrap.execSql(
                    "delete from search_views where title = ?", (title,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    # ---------- Miscellaneous ----------

    _CAPABILITIES = {
        "rebuild": 1,
        "compactify": 1,     # = sqlite vacuum
        "versioning": 1,     # TODO (old versioning)
        "plain text import":1,
#         "asynchronous commit":1  # Commit can be done in separate thread, but
#                 # calling any other function during running commit is not allowed
        }


    def checkCapability(self, capkey):
        """
        Check the capabilities of this WikiData implementation.
        The capkey names the capability, the function returns normally
        a version number or None if not supported
        """
        return WikiData._CAPABILITIES.get(capkey, None)


        # TODO drop and recreate tables and indices!
    def clearCacheTables(self):
        """
        Clear all tables in the database which contain non-essential
        (cache) information as well as other cache information.
        Needed before rebuilding the whole wiki
        """
        DbStructure.recreateCacheTables(self.connWrap)
        self.connWrap.syncCommit()

        self.cachedContentNames = None
        self.cachedGlobalProps = None


    def setPresentationBlock(self, word, datablock):
        """
        Save the presentation datablock (a byte string) for a word to
        the database.
        """
        try:
            self.connWrap.execSql(
                    "update wikiwordcontent set presentationdatablock = ? where "
                    "word = ?", (sqlite.Binary(datablock), word))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    def getPresentationBlock(self, word):
        """
        Returns the presentation datablock (a byte string).
        The function may return either an empty string or a valid datablock
        """
        try:
            return self.connWrap.execSqlQuerySingleItem(
                    "select presentationdatablock from wikiwordcontent where word = ?",
                    (word,))
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbReadAccessError(e)


    def testWrite(self):
        """
        Test if writing to database is possible. Throws a DbWriteAccessError
        if writing failed.
        TODO !
        """
        pass


    def close(self):
        self.connWrap.syncCommit()
        self.connWrap.close()

        self.connWrap = None


    # ---------- Versioning (optional) ----------
    # Must be implemented if checkCapability returns a version number
    #     for "versioning".
        
    def storeModification(self, word):
        """ Store the modification for a single word (wikicontent and headversion for the word must exist)
        between wikicontents and headversion in the changelog.
        Does not modify headversion. It is recommended to not call this directly

        Values for the op-column in the changelog:
        0 set content: set content as it is in content column
        1 modify: content is a binary compact diff as defined in StringOps,
            apply it to new revision to get the old one.
        2 create page: content contains data of the page
        3 delete page: content is undefined
        """

        content, moddate = self._getContentAndInfo(word)[:2]

        headcontent, headmoddate = self.connWrap.execSqlQuery("select content, modified from headversion "+\
                "where word=?", (word,))[0]

        bindiff = getBinCompactForDiff(content, headcontent)
        self.connWrap.execSql("insert into changelog (word, op, content, moddate) values (?, ?, ?, ?)",
                (word, 1, sqlite.Binary(bindiff), headmoddate))  # Modify  # TODO: Support overwrite
        return self.connWrap.lastrowid


    def hasVersioningData(self):
        """
        Returns true iff any version information is stored in the database
        """
        return DbStructure.hasVersioningData(self.connWrap)


    def storeVersion(self, description):
        """
        Store the current version of a wiki in the changelog

        Values for the op-column in the changelog:
        0 set content: set content as it is in content column
        1 modify: content is a binary compact diff as defined in StringOps,
            apply it to new revision to get the old one.
        2 create page: content contains data of the page
        3 delete page: content is undefined

        Renaming is not supported directly.
        """
        # Test if tables were created already

        if not DbStructure.hasVersioningData(self.connWrap):
            # Create the tables
            self.connWrap.syncCommit()
            try:
                DbStructure.createVersioningTables(self.connWrap)
                # self.connWrap.commit()
            except:
                self.connWrap.rollback()
                raise

        self.connWrap.syncCommit()
        try:
            # First move head version to normal versions
            headversion = self.connWrap.execSqlQuery("select description, "+\
                    "created from versions where id=0") # id 0 is the special head version
            if len(headversion) == 1:
                firstchangeid = self.connWrap.execSqlQuerySingleItem("select id from changelog order by id desc limit 1 ",
                        default = -1) + 1

                # Find modified words
                modwords = self.connWrap.execSqlQuerySingleColumn("select headversion.word from headversion inner join "+\
                        "wikiwordcontent on headversion.word = wikiwordcontent.word where "+\
                        "headversion.modified != wikiwordcontent.modified")

                for w in modwords:
                    self.storeModification(w)


                # Store changes for deleted words
                self.connWrap.execSql("insert into changelog (word, op, content, moddate) "+\
                        "select word, 2, content, modified from headversion where "+\
                        "word not in (select word from wikiwordcontent)")

                # Store changes for inserted words
                self.connWrap.execSql("insert into changelog (word, op, content, moddate) "+\
                        "select word, 3, x'', modified from wikiwordcontent where "+\
                        "word not in (select word from headversion)")

                if firstchangeid == (self.connWrap.execSqlQuerySingleItem("select id from changelog order by id desc limit 1 ",
                        default = -1) + 1):

                    firstchangeid = -1 # No changes recorded in changelog

                headversion = headversion[0]
                self.connWrap.execSql("insert into versions(description, firstchangeid, created) "+\
                        "values(?, ?, ?)", (headversion[0], firstchangeid, headversion[1]))

            self.connWrap.execSql("insert or replace into versions(id, description, firstchangeid, created) "+\
                    "values(?, ?, ?, ?)", (0, description, -1, time()))

            # Copy from wikiwordcontent everything to headversion
            self.connWrap.execSql("delete from headversion")
            self.connWrap.execSql("insert into headversion select * from wikiwordcontent")

            self.connWrap.commit()
        except:
            self.connWrap.rollback()
            raise


    def getStoredVersions(self):
        """
        Return a list of tuples for each stored version with (<id>, <description>, <creation date>).
        Newest versions at first
        """
        # Head version first
        result = self.connWrap.execSqlQuery("select id, description, created "+\
                    "from versions where id == 0")

        result += self.connWrap.execSqlQuery("select id, description, created "+\
                    "from versions where id != 0 order by id desc")
        return result


    # TODO: Wrong moddate?
    def applyChange(self, word, op, content, moddate):
        """
        Apply a single change to wikiwordcontent. word, op, content and modified have the
        same meaning as in the changelog table
        """
        if op == 0:
            self.setContentRaw(word, content, moddate)
        elif op == 1:
            self.setContentRaw(word, applyBinCompact(self.getContent(word), content), moddate)
        elif op == 2:
            self.setContentRaw(word, content, moddate)
        elif op == 3:
            self.deleteContent(word)


    # TODO: Wrong date?, more efficient
    def applyStoredVersion(self, id):
        """
        Set the content back to the version identified by id (retrieved by getStoredVersions).
        Only wikiwordcontent is modified, the cache information must be updated separately
        """

        self.connWrap.syncCommit()
        try:
            # Start with head version
            self.connWrap.execSql("delete from wikiwordcontent") #delete all rows
            self.connWrap.execSql("insert into wikiwordcontent select * from headversion") # copy from headversion

            if id != 0:
                lowestchangeid = self.connWrap.execSqlQuerySingleColumn("select firstchangeid from versions where id == ?",
                        (id,))
                if len(lowestchangeid) == 0:
                    raise WikiFileNotFoundException()  # TODO: Better exception

                lowestchangeid = lowestchangeid[0]

                changes = self.connWrap.execSqlQuery("select word, op, content, moddate from changelog "+\
                        "where id >= ? order by id desc", (lowestchangeid,))

                for c in changes:
                    self.applyChange(*c)


            self.connWrap.commit()
        except:
            self.connWrap.rollback()
            raise


    def deleteVersioningData(self):
        """
        Completely delete all versioning information
        """
        DbStructure.deleteVersioningTables(self.connWrap)


    # ---------- Other optional functionality ----------

    def cleanupAfterRebuild(self, progresshandler):
        """
        Rebuild cached structures, try to repair database inconsistencies.

        Must be implemented if checkCapability returns a version number
        for "rebuild".
        
        progresshandler -- Object, fulfilling the GuiProgressHandler
            protocol
        """
#         # get all of the wikiWords
#         wikiWords = self.getAllPageNamesFromDisk()   # Replace this call
#                 
#         progresshandler.open(len(wikiWords) + 1)
#         try:
#             step = 1
#     
#             # re-save all of the pages
#             self.clearCacheTables()
#             for wikiWord in wikiWords:
#                 progresshandler.update(step, u"")   # , "Rebuilding %s" % wikiWord)
#                 wikiPage = self.createPage(wikiWord)
#                 wikiPage.update(wikiPage.getContent(), False)  # TODO AGA processing
#                 step = step + 1

        try:
            self.connWrap.execSql("update wikiwordcontent "
                    "set wordnormcase=utf8Normcase(word)")
            DbStructure.rebuildIndices(self.connWrap)

            # TODO
            # Check the presence of important indexes

            indexes = self.connWrap.execSqlQuerySingleColumn(
                    "select name from sqlite_master where type='index'")
            indexes = map(string.upper, indexes)

            if not "WIKIWORDCONTENT_PKEY" in indexes:
                # Maybe we have multiple pages with the same name in the database
                
                # Copy valid creation date to all pages
                self.connWrap.execSql("update wikiwordcontent set "
                        "created=(select max(created) from wikiwordcontent as "
                        "inner where inner.word=wikiwordcontent.word)")
    
                # Delete all but the newest page
                self.connWrap.execSql("delete from wikiwordcontent where "
                        "ROWID not in (select max(ROWID) from wikiwordcontent as "
                        "outer where modified=(select max(modified) from "
                        "wikiwordcontent as inner where inner.word=outer.word) "
                        "group by outer.word)")
    
                DbStructure.rebuildIndices(self.connWrap)
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


       # TODO: More repair operations


#         # recreate word caches
#         self.cachedContentNames = {}
#         for word in self.getAllDefinedContentNames():
#             self.cachedContentNames[word] = 1
# 
#         # cache aliases
#         aliases = self.getAllAliases()
#         for alias in aliases:
#             self.cachedContentNames[alias] = 2


#         finally:            
#             progresshandler.close()


    def commit(self):
        """
        Do not call from this class, only from outside to handle errors.
        """
        try:
            self.connWrap.commit()
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    def rollback(self):
        """
        Do not call from this class, only from outside to handle errors.
        """
        try:
            self.connWrap.rollback()
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)


    def vacuum(self):
        """
        Reorganize the database, free unused space.
        
        Must be implemented if checkCapability returns a version number
        for "compactify".        
        """
        try:
            self.connWrap.syncCommit()
            self.connWrap.execSql("vacuum")
        except (IOError, OSError, sqlite.Error), e:
            traceback.print_exc()
            raise DbWriteAccessError(e)



    # TODO: Better error checking
    def copyWikiFilesToDatabase(self):
        """
        Helper to transfer wiki files into database for migrating from
        original WikidPad to specialized databases.

        Must be implemented if checkCapability returns a version number
        for "plain text import".
        """
        self.connWrap.syncCommit()

        fnames = glob.glob(pathEnc(join(self.dataDir, '*.wiki')))
        for fn in fnames:
            word = pathDec(basename(fn)).replace('.wiki', '')

#             fp = open(fn)
#             content = fp.read()
#             fp.close()
#             content = fileContentToUnicode(content)
            content = fileContentToUnicode(loadEntireTxtFile(fn))
            if self.wikiDocument.getFormatting().isNakedWikiWord(word):
                self.setContent(word, content, moddate=stat(fn).st_mtime)

        self.connWrap.commit()


def listAvailableWikiDataHandlers():
    """
    Returns a list with the names of available handlers from this module.
    Each item is a tuple (<internal name>, <descriptive name>)
    """
    if sqlite is not None:
        return [("compact_sqlite", "Compact Sqlite")]
    else:
        return []


def getWikiDataHandler(name):
    """
    Returns a creation function (or class) for an appropriate
    WikiData object and a createWikiDB function or (None, None)
    if name is unknown
    """
    if name == "compact_sqlite":
        return WikiData, createWikiDB
    
    return (None, None)