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
ALL_TIMEFRAMES = ['1w', '1d', '4h', 'h']
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
    data = dataList[ALL_TIMEFRAMES.index('1d')]
    stats = Struct(exchange=data.exchange.name,
                   marketStr=marketStr,
                   symbolKey=data.symbolKey,
                   dataList=dataList)

    if all([data.count() for data in dataList]):
        # Calculate stats:
        stats.bb = []
        stats.bbOver = []
        stats.ma = []
        stats.adx = []
        stats.stepsSinceSqueeze = []
        stats.squeezeDuration = []
        stats.upTrend = []
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

        dataList = [weekly, daily, hour4, hourly]
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
        col = section
        if col < len(columns):
            colName = columns[col]
        else:
            col -= len(columns)
            col, subCol = col/NUM_TIMEFRAME_COLS, col%NUM_TIMEFRAME_COLS
            colName = '\n' + ALL_TIMEFRAMES[subCol]
            if not subCol:
                colName = multiColumns[col] + colName

        return colName

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return

        sortedMarkets = self.window.sortedMarkets
        row, col = index.row(), index.column()

        columns, multiColumns = self.window.activeColumns()
        if col < len(columns):
            colName = columns[col]
            subCol = 0
        else:
            col -= len(columns)
            col, subCol = col/NUM_TIMEFRAME_COLS, col%NUM_TIMEFRAME_COLS
            colName = multiColumns[col]

        if row >= len(sortedMarkets):
            return
        stats = sortedMarkets[row]

        ret = None
        if stats:
            if role == Qt.DisplayRole:
                if colName == 'marketStr':
                    ret = stats.marketStr
                    ret = ret[:ret.rfind('/')]# Cut off the exchange name
                elif colName == 'volume':
                    ret = strVolume(stats.volume)
                elif colName == 'upTrend':
                    ret = ['SHORT', 'LONG'][stats.upTrend[subCol]]
                else:
                    ret = getattr(stats, multiColumns[col], '')[subCol]
                    if type(ret) != str:
                        ret = '{0:.2f}'.format(ret)
            if role == Qt.ForegroundRole:
                if colName == 'stepsSinceSqueeze':
                    if not stats.stepsSinceSqueeze[subCol]:
                        ret = QtGui.QColor(QtCore.Qt.red)
            if role == Qt.BackgroundRole:
                if colName == 'upTrend':
                    ret = QtGui.QColor([QtCore.Qt.red, QtCore.Qt.cyan][stats.upTrend[subCol]])

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

        self.selectedMarket = None
        self.model = WatchlistModel(self)
        self.ui.tableView.setModel(self.model)
        self.ui.tableView.setAlternatingRowColors(True)
        self.ui.tableView.horizontalHeader().setResizeMode(QtGui.QHeaderView.Stretch)
        self.ui.tableView.mouseMoveEvent = self.listMouseMoveEvent
        self.ui.tableView.setMouseTracking(True)

        selectionModel = self.ui.tableView.selectionModel()
        selectionModel.selectionChanged.connect(self.listSelectionChanged)

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

        self.onSelectColumns()

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

        # Override the Stretch just for the marketStr column
        columns, multiColumns = self.activeColumns()
        mode = QtGui.QHeaderView.ResizeToContents if len(columns) and columns[0] == 'marketStr' else QtGui.QHeaderView.Stretch
        self.ui.tableView.horizontalHeader().setResizeMode(0, mode)

    def loadWatchlists(self):
        o = Struct(watchlists={})
        def addWatchlist(fromExchanges, name=None):
            fromExchanges = [exchanges.findExchange(e) for e in wrapList(fromExchanges)]

            name = name or fromExchanges[0].name
            o.watchlists[name] = []
            for exchange in fromExchanges:
                o.watchlists[name] += [s + ' / ' + exchange.name for s in exchange.symbols]

        def addRuntimeWatchlist(name):
            o.watchlists[name] = []

        addWatchlist(['Google', 'Yahoo'], 'Builtin')
        addWatchlist('Poloniex')
        addRuntimeWatchlist(ig.WATCHLIST_OPEN_ORDERS)
        addRuntimeWatchlist(WATCHLIST_SCREENER)

        for name in ig.IG_INDEX_MAP.values():
            watchlist = gCache.get(cacheKey(name))
            if watchlist:
                o.watchlists[name] = watchlist.value

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

        method = str(self.ui.bbOverFilter.currentText()).lower()
        stepsSinceSqueeze = 'since squeeze' in method
        squeezeDuration = 'squeeze duration' in method
        comboBBVsLongerMA = 'longer ma' in method
        comboBBFuncs = 'functions' in method
        dailyVolume = 'volume' in method

        def calcSortKey(stats):
            ret = -1. # Filter it out
            if dailyVolume:
                ret = stats.volume
            elif comboBBVsLongerMA:
                for i in range(1):
                    bb = stats.bbOver[i]
                    if stats.bb[i] > stats.ma[i]:   # If in an uptrend
                        bb *= -1                   # then favour oversold
                    if bb < 0.7:
                        return ret
                ret = 1.
            elif stepsSinceSqueeze:
                ret = sum([1000000 - stats.stepsSinceSqueeze[i] + stats.squeezeDuration[i]/1000000.0 for i in range(len(stats.stepsSinceSqueeze))])
            elif squeezeDuration:
                ret = stats.squeezeDuration
            else:
                ret = stats.marketStr

            return ret

        sharedD = self.sharedD
        if len(self.results) == sharedD['idx']:
            return# Model is up to date

        sharedD['pause'] = self.ui.pauseRefresh.isChecked()
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

        results = []
        for stats in self.results:
            if not hasattr(stats, 'bbOver'):# Test for one of the stats
                continue
            if method == 'no sorting':
                stats.sortKey = -len(results)
            else:
                stats.sortKey = calcSortKey(stats)
                if stats.sortKey < 0.:
                    continue
            results.append(stats)

        oldLen = self.model.rowCount(None)
        self.sortedMarkets = sorted(results, key=lambda stats:stats.sortKey, reverse=True)
        newLen = self.model.rowCount(None)
        self.model.beginInsertRows(QtCore.QModelIndex(), oldLen, newLen-1)
        self.model.endInsertRows()

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
            app = QtGui.QApplication([])# Needed for error message dialogs
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
