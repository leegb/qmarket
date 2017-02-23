#!/usr/bin/python

from pyqtgraph import QtCore, QtGui
Qt = QtCore.Qt
import signal, sys, time, urllib, datetime as dt
from multiprocessing import Process, Manager
from utils import *
import exchanges, ig
from chartdata import *
import watchlist_ui # pyuic4 watchlist.ui > watchlist_ui.py

CACHE_SECONDS = 60 * 60 # Re-download if longer than this
ALL_TIMEFRAMES = ['4h', '1d', '1w']
NUM_TIMEFRAME_COLS = 3

WATCHLIST_SCREENER = 'Screener'

def getScreenerWatchlist():
    mil = '000000'
    query = '(exchange == "LON") ' +\
        '& (market_cap >= 5'+mil+') ' +\
        '& (market_cap <= 100'+mil+') ' +\
        '& (change_today_percent >= 1) ' +\
        '& (volume >= 10'+mil+')'
    query = urllib.quote(query)
    url = 'https://www.google.co.uk/finance?output=json&start=0&num=100&noIL=1&q=[' + query + ']&restype=company&sortas=' +\
        'QuotePercChange'
        #'Volume'
    response = requests.get(url)

    # Remove "original_query" as it breaks json parsing
    content = response.content
    idx = content.index('"original_query"')
    idx2 = content.index('\n', idx)
    content = content[:idx] + content[idx2:]
    content = content.replace('\\x26', '&') # Also breaks parsing
    content = json.loads(content)

    watchlist = []
    for result in content['searchresults']:
        watchlist.append(result['title'] + '|' + result['exchange'] + ':' + result['ticker'] + ' / Google')
    return watchlist

def calcStatsFromData(dataList, marketStr):
    data = dataList[0]
    stats = Struct(exchange=data.exchange.name,
                   marketStr=marketStr,
                   symbolKey=data.symbolKey,
                   dataList=dataList,
                   bb=[],
                   bbOver=[],
                   ma=[],
                   adx=[],
                   squeezeState=[],
                   stepsSinceSqueeze=[],
                   squeezeDuration=[],
                   upTrend=[])

    if all([data.count() for data in dataList]):
        # Calculate stats:
        for data in dataList:
            bb = data.bbMean            # MA-20
            bbOver = data.bbOver
            ma = data.taMA2[0].yColumns  # MA-50
            adx = data.taADX[0].yColumns

            last = data.count()-1
            stats.bb.append(bb[last])
            stats.bbOver.append(bbOver[last])
            stats.ma.append(ma[last])
            stats.adx.append(adx[last])
            stats.upTrend.append(data.upTrend[-1])

            stats.squeezeState.append(data.squeezeState)
            stats.stepsSinceSqueeze.append(data.stepsSinceSqueeze)
            stats.squeezeDuration.append(data.squeezeDuration)

    return stats

def procRefreshWatchlist(sharedD, watchlistName, watchlist):

    def refreshMarketStats(marketStr):
        market = parseToMarketStruct(marketStr)

        appendMinuteData = market.exchange.appendMinuteData
        resampleMinuteData = False

        market.timeframe = 'h'
        hourly = ChartData(market)
        market.timeframe = 'd'
        daily = ChartData(market)

        dataList = [hourly, daily]
        for data in dataList:
            data.downloadAndParse()

        if appendMinuteData or resampleMinuteData:
            market.timeframe = 'm'
            minuteData = ChartData(market)
            minuteData.downloadAndParse()
            for data in dataList:
                data.appendMinuteData(minuteData)

        hour4 = hourly.resampleNew('4h')
        weekly = daily.resampleNew('1w')

        dataList = [hour4, daily, weekly]
        for data in dataList:
            data.calcIndicatorsMakePlots()

        stats = calcStatsFromData(dataList, marketStr)

        stats.volume = 0.
        if hourly.isOHLC:
            # Resample hourly to get 24-hour volume
            data = hourly.resampleNew('1d')
            last = data.count()-1
            stats.volume = data.volume[last]

        return stats

    stats = None
    while not sharedD['abort'] and sharedD['idx'] < len(watchlist):
        if sharedD['pause']:
            time.sleep(0.1)
            continue

        marketStr = watchlist[sharedD['idx']]
        key = cacheKey(watchlistName, marketStr)
        stats = gCache.get(key)
        if not stats or (dt.datetime.now() - stats.time).seconds > CACHE_SECONDS:
            stats = refreshMarketStats(marketStr)
            gCache.set(key, stats)

        sharedD['idx'] += 1

# Return: float, string
def getStatsValue(stats, colName, subCol):
    if colName == 'marketStr':
        ret = stats.marketStr
        ret = None, ret[:ret.rfind('/')]# Cut off the exchange name
    elif colName == 'volume':
        ret = stats.volume, strVolume(stats.volume)
    elif colName == 'upTrend':
        ret = None, ['SHORT', 'LONG'][stats.upTrend[subCol]]
    elif colName == 'stepsSinceSqueeze':
        ret = stats.stepsSinceSqueeze[subCol], stats.squeezeState[subCol]
    else:
        ret = getattr(stats, colName, '')[subCol]
        if type(ret) == str:
            ret = None, ret
        else:
            ret = ret, '{0:.2f}'.format(ret)
    return ret

def getColumnNameAndSub(columns, multiColumns, colIndex):
    if colIndex < len(columns):
        subCol = None
        colName = columns[colIndex]
    else:
        colIndex -= len(columns)
        colIndex, subCol = colIndex/NUM_TIMEFRAME_COLS, colIndex%NUM_TIMEFRAME_COLS
        colName = multiColumns[colIndex]
    return colName, subCol

class WatchlistModel(QtCore.QAbstractTableModel):
    columns = [
        'marketStr',
        'volume',
    ]
    multiColumns = [
        'adx',
        'stepsSinceSqueeze',
        'upTrend',
        #'squeezeDuration',
        #'bbOver',
    ]

    def __init__(self, window, *args, **kwargs):
        super(WatchlistModel, self).__init__(*args, **kwargs)
        self.window = window

    def rowCount(self, parent):
        return len(self.window.sortedMarkets)

    def columnCount(self, parent):
        columns, multiColumns = self.window.activeColumns()
        return len(columns) + NUM_TIMEFRAME_COLS*len(multiColumns)

    def headerData(self, section, orientation, role):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return
        columns, multiColumns = self.window.activeColumns()
        colName, subCol = getColumnNameAndSub(columns, multiColumns, section)
        if subCol != None:
            colName = (colName if subCol == 0 else '') + '\n' + ALL_TIMEFRAMES[subCol]
        ascending = self.window.sortColumns.get(section, None)
        if ascending == True:
            colName += u'\u2191'#Up arrow
        elif ascending == False:
            colName += u'\u2193'#Down arrow
        return colName

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return
        row, col = index.row(), index.column()

        columns, multiColumns = self.window.activeColumns()
        colName, subCol = getColumnNameAndSub(columns, multiColumns, col)

        ret = None
        stats = self.window.sortedMarkets[row]
        numVal, strVal = getStatsValue(stats, colName, subCol)
        if role == Qt.DisplayRole:
            ret = strVal
        #elif role == Qt.ForegroundRole:
        elif role == Qt.BackgroundRole:
            if colName == 'upTrend':
                ret = QtGui.QColor([QtCore.Qt.red, QtCore.Qt.cyan][stats.upTrend[subCol]])
            elif colName == 'stepsSinceSqueeze':
                if strVal == 'Squeeze':
                    ret = QtGui.QColor(QtCore.Qt.red)
                elif 'Fired' in strVal:
                    ret = QtGui.QColor(QtCore.Qt.cyan)
        return QtCore.QVariant(ret)

# Return either the watchlist or one of its calculated stats
def cacheKey(name, entry='watchlist'):
    return ['watchlists', name, entry]

class WatchlistWindow(QtGui.QMainWindow):
    def __init__(self, cg):
        super(WatchlistWindow, self).__init__()

        self.cg = cg
        self.ui = watchlist_ui.Ui_WatchlistWindow()
        self.ui.setupUi(self)

        for c, col in enumerate(WatchlistModel.columns + WatchlistModel.multiColumns):
            checkbox = QtGui.QCheckBox(col)
            checkbox.clicked.connect(self.onSelectColumns)
            self.ui.showColumns.layout().addWidget(checkbox)
            setattr(self.ui, col + '_checkbox', checkbox)# for guiSave()

        self.sortedMarkets = []
        self.sortColumns = {}
        self.recalcSortKeys = False
        self.selectedMarket = None
        self.ui.tableView.setAlternatingRowColors(True)
        header = self.ui.tableView.horizontalHeader()
        header.sectionClicked.connect(self.onHeaderSectionClicked)
        header.setResizeMode(QtGui.QHeaderView.Stretch)

        self.ui.tableView.mouseMoveEvent = self.listMouseMoveEvent
        self.ui.tableView.setMouseTracking(True)

        for i in range(len(ALL_TIMEFRAMES)):
            self.ui.showCharts.addItem(', '.join(ALL_TIMEFRAMES[:i+1]))
        for i in range(-len(ALL_TIMEFRAMES)+1, 0):
            self.ui.showCharts.addItem(', '.join(ALL_TIMEFRAMES[i:]))

        self.loadWatchlists()

        firstOne = None
        for name,watchlist in sorted(self.watchlists.items()):
            item = name
            if len(watchlist):
                item += ' (%i)' % len(watchlist)
            firstOne = item if name == 'Builtin' else firstOne
            self.ui.watchlistName.addItem(item)

        guiRestore(self.ui, gSettings)

        # Set the default selected item
        #self.ui.watchlistName.setCurrentIndex(self.ui.watchlistName.findText(firstOne))

        self.procRefresh = None
        self.ui.watchlistName.currentIndexChanged.connect(self.onWatchlistSelected)

        self.model = WatchlistModel(self)
        self.ui.tableView.setModel(self.model)
        selectionModel = self.ui.tableView.selectionModel()
        selectionModel.selectionChanged.connect(self.listSelectionChanged)

    def activeColumns(self):
        columns, multiColumns = [], []
        for checkbox in self.ui.showColumns.children()[1:]:
            if not checkbox.isChecked():
                continue
            text = str(checkbox.text())
            if text in WatchlistModel.columns:
                columns.append(text)
            elif text in WatchlistModel.multiColumns:
                multiColumns.append(text)
        return columns, multiColumns

    def onSelectColumns(self):
        # All information previously retrieved is invalid, including rowCount() and data()
        self.model.modelReset.emit()
        self.sortColumns = {}
        self.recalcSortKeys = True
    def onHeaderSectionClicked(self, logicalIndex):
        oldValue = self.sortColumns.get(logicalIndex, False)
        if not app.keyboardModifiers() & Qt.ControlModifier:
            self.sortColumns = {}
        self.sortColumns[logicalIndex] = not oldValue
        self.recalcSortKeys = True

    def loadWatchlists(self):
        o = Struct(watchlists={})
        def addWatchlistFromEx(fromExchanges, name=None):
            fromExchanges = [exchanges.findExchange(e) for e in wrapList(fromExchanges)]

            name = name or fromExchanges[0].name
            o.watchlists[name] = []
            for exchange in fromExchanges:
                o.watchlists[name] += [s + ' / ' + exchange.name for s in exchange.symbols]

        addWatchlistFromEx(['Google', 'Yahoo'], 'Builtin')
        addWatchlistFromEx('Poloniex')

        def addWatchlistFromList(name, List=[]):
            o.watchlists[name] = List

        addWatchlistFromList(ig.WATCHLIST_OPEN_ORDERS)
        addWatchlistFromList(WATCHLIST_SCREENER)

        for name in ig.IG_WATCHLISTS:
            watchlist = gCache.get(cacheKey(name))
            if watchlist:
                addWatchlistFromList(name, watchlist.value)

        self.watchlists = o.watchlists

    def onWatchlistSelected(self, index=None):
        self.joinThread()

        name = str(self.ui.watchlistName.currentText())
        name = name.split(' (')[0] # Remove members count

        if name == 'Poloniex':
            exchanges.savePoloniexMarkets()
            self.loadWatchlists()

        if name == ig.WATCHLIST_OPEN_ORDERS:
            watchlist = ig.getOpenPositions()
            #watchlist = ig.getWorkingOrders()
        elif name == WATCHLIST_SCREENER:
            watchlist = getScreenerWatchlist()
        else:
            watchlist = self.watchlists[name]

        manager = Manager()
        self.sharedD = manager.dict(abort=False, pause=False, idx=0)
        self.results = []
        self.watchlistName = name
        self.watchlist = watchlist
        self.procRefresh = Process(target=procRefreshWatchlist, args=(self.sharedD, self.watchlistName, self.watchlist))
        self.procRefresh.start()

    def startWatchlist(self):
        self.onWatchlistSelected()

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.onRefreshTable)
        self.timer.start(100)

    def joinThread(self):
        if self.procRefresh:
            self.sharedD['abort'] = True
            self.procRefresh.join()

    def closeEvent(self, event):
        self.joinThread()
        guiSave(self.ui, gSettings)

    def onRefreshTable(self):
        sharedD = self.sharedD
        sharedD['pause'] = self.ui.pauseRefresh.isChecked()

        if not self.recalcSortKeys and len(self.results) == sharedD['idx']:
            return# Model is up to date
        self.recalcSortKeys = False

        while len(self.results) < sharedD['idx']:
            marketStr = self.watchlist[len(self.results)]
            key = cacheKey(self.watchlistName, marketStr)
            stats = gCache.get(key).value

            # When we unpickle, the DataFrame is valid but ChartData is not so call the constructor.
            market = parseToMarketStruct(marketStr)
            for i,data in enumerate(stats.dataList):
                market.timeframe = ALL_TIMEFRAMES[i]
                data.__init__(market, existingDf=data)
            self.results.append(stats)

        columns, multiColumns = self.activeColumns()
        results = []
        for stats in self.results:
            if not stats.bb:
                continue# Test that stats are valid - see calcStatsFromData()

            stats.sortKey = ()
            for col in sorted(self.sortColumns):
                colName, subCol = getColumnNameAndSub(columns, multiColumns, col)
                numVal, strVal = getStatsValue(stats, colName, subCol)
                ascending = self.sortColumns[col]
                if numVal != None:
                    stats.sortKey += (numVal * (-1. if ascending else 1.),)
                else:
                    strVal = strVal.lower()
                    stats.sortKey += (''.join([chr(255-ord(c)) for c in strVal]) if ascending else strVal,)

            results.append(stats)

        oldLen = self.model.rowCount(None)
        self.sortedMarkets = sorted(results, key=lambda stats:stats.sortKey, reverse=True)
        newLen = self.model.rowCount(None)
        self.model.beginInsertRows(QtCore.QModelIndex(), oldLen, newLen-1)
        self.model.endInsertRows()

        # Override the Stretch just for the marketStr column
        mode = QtGui.QHeaderView.ResizeToContents if len(columns) and columns[0] == 'marketStr' else QtGui.QHeaderView.Stretch
        self.ui.tableView.horizontalHeader().setResizeMode(0, mode)

        self.ui.statusbar.clearMessage()
        self.ui.statusbar.showMessage('Refreshed %i/%i' % (sharedD['idx'], len(self.watchlist)))

    def getStatsByRow(self, row):
        if row < 0 or row >= len(self.sortedMarkets):
            return
        return self.sortedMarkets[row]

    def getChartsToShow(self):
        return [s.strip() for s in str(self.ui.showCharts.currentText()).split(',')]

    def listMouseMoveEvent(self, event):
        pos = event.pos()
        index = self.ui.tableView.indexAt(pos)
        stats = self.getStatsByRow(index.row())
        if not stats:
            return

        # Create candlestick pictures on list mouseover to speed up selection.
        for text in self.getChartsToShow():
            data = stats.dataList[ALL_TIMEFRAMES.index(text)]
            for isVolume in range(2):
                data.createCandlestick(isVolume, self.cg.showTrendBars)

    def listSelectionChanged(self, selected, deselected):
        indexes = selected.indexes()
        if not len(indexes) or not indexes[0].isValid():
            return
        stats = self.getStatsByRow(indexes[0].row())
        if not stats:
            return
        self.selectedMarket = stats

        cg = self.cg
        chartsToShow = self.getChartsToShow()
        for i in range(len(ALL_TIMEFRAMES)):
            coord = (cg.coord[0], cg.coord[1] + i)
            if i < len(chartsToShow):
                text = chartsToShow[i]
                data = stats.dataList[ALL_TIMEFRAMES.index(text)]
                cg.window.setChartAt(data, coord)
            else:
                chart = cg.window.charts.get(coord)
                if chart: cg.window.removeChart(chart)

def doRefresh():
    clearConsole = lambda: os.system('clear')
    outer = makeThreadStruct()

    def signal_handler(signal, frame):
        outer.abort = True
    signal.signal(signal.SIGINT, signal_handler)

    thread = Thread(target=lambda: procRefreshWatchlist(outer))
    thread.start()
    while thread.isAlive():
        time.sleep(0.1)
        clearConsole()
        print 'Refreshed %i/%i' % (outer.idx, len(outer.watchlist))
        print 'Top scoring markets:'
        sortedMarkets = sorted(outer.results, key=lambda s: s.buildup, reverse=True)
        columns = self.columns
        print columns
        for m in sortedMarkets[:20]:
            for c in columns:
                print str(m.__dict__[c]).ljust(15),
            print

def main():
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        cmd = argv[i]
        if cmd in ['--refresh', '-r']:
            doRefresh()
        elif cmd == '--ig':
            ig.importIGIndexEpicsWatchlist()
        elif cmd == '--polo':
            exchanges.savePoloniexMarkets()
        elif i < len(argv) - 1:
            arg1 = argv[i+1]
            if cmd == '--igcsv':
                ig.importIGIndexCSVWatchlist(arg1)
        i += 1

if __name__ == '__main__':
    main()
