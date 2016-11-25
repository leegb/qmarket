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

    def show(self):

        self.edit.show()
        self.edit.setFocus()

        # User can supply a fixed set of results, so ask for them.
        self.textChanged('')

    def textChanged(self, newText):
        newText = unicode(newText)
        for s in self.model.stringList():
            if newText.lower() == unicode(s).lower():
                return

        stringList = self.onSearchString(newText) if newText else []
        self.model.setStringList(stringList)

        self.completer.complete()# Copy-pasting doesnt trigger completion

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
