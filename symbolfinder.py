from utils import *
from pyqtgraph import QtCore, QtGui
Qt = QtCore.Qt
import requests, json

class SymbolFinder():
    def __init__(self, parentWidget,
                 onSearchString,
                 onChooseSearchResult):

        self.onChooseSearchResult = onChooseSearchResult
        self.onSearchString = onSearchString

        completer = QtGui.QCompleter()
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setMaxVisibleItems(20)
        completer.setCompletionMode(QtGui.QCompleter.UnfilteredPopupCompletion)
        self.model = QtGui.QStringListModel()
        completer.setModel(self.model)

        self.edit = edit = QtGui.QLineEdit(parentWidget)
        edit.setMinimumWidth(600)
        edit.setCompleter(completer)

        edit.textChanged.connect(self.textChanged)
        edit.returnPressed.connect(self.returnPressed)
        edit.keyPressEvent = self.keyPressEvent
        self.completer = completer
        self.threads = []

    def show(self):
        self.edit.show()
        self.edit.setFocus()

        # User can supply a fixed set of results, so ask for them.
        self.textChanged('')

    def onResultsFetched(self):
        results = self.threads[-1].results
        self.model.setStringList(results)
        self.completer.complete()# Copy-pasting doesnt trigger completion

    def textChanged(finder, newText):
        newText = unicode(newText)
        for s in finder.model.stringList():
            if newText.lower() == unicode(s).lower():
                return

        class Thread(QtCore.QThread):
            def run(self):
                if not newText: return
                try: self.results = finder.onSearchString(newText)
                except: return

        thread = Thread()
        thread.results = []
        finder.threads.append(thread)
        thread.finished.connect(finder.onResultsFetched)
        thread.start()

    def closeEditBox(self):
        self.edit.hide()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.closeEditBox()
            return

        QtGui.QLineEdit.keyPressEvent(self.edit, e)

    def returnPressed(self):
        popup = self.completer.popup()
        index = popup.currentIndex()
        if not index.isValid():
            index = self.model.index(0, 0)# Pick the first one in the list

        if index.isValid():
            row = index.row()
            s = str(index.data().toString())
            self.onChooseSearchResult(s)

        self.closeEditBox()
