import os, inspect, datetime as dt
from pyqtgraph import QtCore, QtGui

ROW,COL = 0,1
OPEN_CLOSE_COLOR = ['red', 'green'] # close >= open

def debug():
    QtCore.pyqtRemoveInputHook()
    from IPython.core.debugger import Tracer; Tracer()()
    #QtCore.pyqtRestoreInputHook()

def now():
    return dt.datetime.now()
def timestamp(dt):
    return int(dt.strftime('%s'))
fromtimestamp = dt.datetime.fromtimestamp
def saveDiv(a, b):
    return a / b if b else a
def wrapList(value):
    return value if type(value) == list else [value]

def intervalSeconds(intervalStr):
    letter = intervalStr[-1]
    return int(intervalStr[0:-1]) * dict(s=1, m=60, h=3600, d=86400, w=604800)[letter]

def strVolume(x, formatPlaces=1):
    formatString = '{:,.%if}'
    if x >= 1000000:
        formatString += 'M'
        x /= 1000000.
    elif x >= 1000:
        formatString += 'K'
        x /= 1000.
    return (formatString % formatPlaces).format(x)

class Struct:
        def __init__(self, **entries): self.__dict__.update(entries)

import pickle, os
class MultiKeyDict(dict):
    def __init__(self, filename=None):
        self.filename = filename
        if self.filename:
            self.load()
    def save(self):
        dir = os.path.dirname(self.filename)
        if dir and not os.path.exists(dir):# Python 3.2 makedirs() has exist_ok
            os.makedirs(dir)
        pickle.dump(dict(self), open(self.filename, 'wb'))
    def load(self):# Called automatically by constructor
        try:
            d = pickle.load(open(self.filename, 'rb'))
            self.update(d)
        except:
            pass

    def get(self, keys):
        keys = self._keyList(keys)
        d = dict(self)
        for k in keys[:-1]:
            v = d.get(k)
            if type(v) != dict:
                return None
            d = v

        k = keys[-1]
        v = d.get(k)
        return v

    def _keyList(self, keys):
        # Key list can be a tuple, list or a single key, in which case wrap it in a list.
        return keys if hasattr(keys, '__iter__') else [keys]

    def set(self, keys, v):
        def setRec(d, keys, v):
            if type(d) != dict:
                d = {}
            k, keys = keys[0], keys[1:]
            if keys:
                d[k] = setRec(d.get(k), keys, v)
            else:
                d[k] = v
            return d

        keys = self._keyList(keys)
        d = dict(self)
        d = setRec(d, keys, v)
        self.update(d)

    def unset(self, keys):
        keys = self._keyList(keys)
        topD = d = dict(self)
        for k in keys[:-1]:
            d = d.get(k)
            if type(d) == None:
                return

        k = keys[-1]
        v = d.get(k)
        if type(v) != None:
            d.pop(k)
            self.update(topD)

class FileCache():
    def __init__(self, baseFolder=None):
        self.baseFolder = baseFolder

    def fullPath(self, keys):
        dirList = [self.baseFolder] + keys
        dirList[-1] += '.pickle'
        return os.path.join(*dirList)

    def get(self, keys):
        fullPath = self.fullPath(keys)
        try:
            return pickle.load(open(fullPath, 'rb'))
        except:
            return None

    def set(self, keys, value):
        fullPath = self.fullPath(keys)
        dir = os.path.dirname(fullPath)
        if dir and not os.path.exists(dir):# Python 3.2 makedirs() has exist_ok
            os.makedirs(dir)
        pickle.dump(Struct(value=value, time=now()), open(fullPath, 'wb'))# Also record the time the value was saved.

CACHE_DIR = os.path.expanduser('~') + '/.qmarket'
gCache = FileCache(CACHE_DIR)
gSettings = QtCore.QSettings('MyCompany', 'qmarket')

def guiSave(ui, settings):
    for name, obj in inspect.getmembers(ui):
        if isinstance(obj, QtGui.QComboBox):
            name = obj.objectName()
            text = str(obj.currentText())
            settings.setValue(name, text)

        if isinstance(obj, QtGui.QLineEdit):
            name = obj.objectName()
            value = obj.text()
            settings.setValue(name, value)

        if isinstance(obj, QtGui.QCheckBox):
            name = obj.objectName()
            state = obj.isChecked()
            settings.setValue(name, state)

def guiRestore(ui, settings):
    for name, obj in inspect.getmembers(ui):
        if isinstance(obj, QtGui.QComboBox):
            index  = obj.currentIndex()
            name   = obj.objectName()

            value = settings.value(name).toString()
            if value == '':
                continue

            index = obj.findText(value)
            if index == -1:
                continue
                obj.insertItems(0,[value])
                index = obj.findText(value)
                obj.setCurrentIndex(index)
            else:
                obj.setCurrentIndex(index)

        if isinstance(obj, QtGui.QLineEdit):
            name = obj.objectName()
            value = settings.value(name).toString()
            obj.setText(value)

        if isinstance(obj, QtGui.QCheckBox):
            name = obj.objectName()
            value = settings.value(name)
            if value != None:
                obj.setChecked(value.toBool())
