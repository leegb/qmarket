#!/usr/bin/python

import pyqtgraph as pg
from pyqtgraph import QtCore, QtGui
Qt = QtCore.Qt
import os, sys, math, types, datetime as dt
import exchanges, symbolfinder, watchlists
from utils import *
from chartdata import *

PLOT_MAIN = 0
PLOT_EXTRA_TA = 1
PLOT_VOLUME = 2
PLOT_ORDERWALL = 3
PLOT_COUNT = 4

SHOW_OPTIONS = [
    ('Orderwall', '&Orderwall'),
    ('Volume', '&Volume'),
    ('ADX', '&ADX'),
    ('MA1', 'MA&1'),
    ('MA2', 'MA&2'),
    ('VWAP', 'V&WAP'),
    ('BB', '&Bollinger Bands'),
    ('BBOver', 'BB Overbought/&sold'),
    ('KC', '&Keltner Channel'),
    ('Pulse', '&Pulse'),
    ('TrendBars', '&Trend Bars'),
    ('LocateBox', '&Locate Box'),
]

class DateAxis(pg.AxisItem):
    SPACING_HOUR =  3600
    SPACING_DAY =   3600*24
    SPACING_WEEK =  3600*24*7
    SPACING_MONTH = 3600*24*30
    SPACING_YEAR =  3600*24*30*12
    SPACING_YEARS = 3600*24*30*24

    def tickStrings(self, values, scale, spacing):
        strns = []
        rng = spacing

        if rng < self.SPACING_DAY:
            format = '%H:%M:%S'
            label1 = '%b %d -'
            label2 = ' %b %d, %Y'
        elif rng < self.SPACING_MONTH:
            format = '%d'
            label1 = '%b - '
            label2 = '%b, %Y'
        elif rng < self.SPACING_YEAR:
            format = '%b' # Month
            label1 = '%Y -'
            label2 = ' %Y'
        #elif rng > self.SPACING_YEARS:
        else:
            format = '%Y'
            label1 = ''
            label2 = ''

        values = self.allTicks[spacing]
        for x in values:
            try:
                strns.append(fromtimestamp(x).strftime(format))
            except ValueError:  ## Can't handle dates before 1970
                strns.append('')

        return strns

    def tickValues(self, minVal, maxVal, size):
        data = self.cg.data
        if not data.count():
            return []

        minVal = max(minVal, data.times[0])
        maxVal = min(maxVal, data.times.iloc[-1])

        tmin = fromtimestamp(minVal)
        tmax = fromtimestamp(maxVal)

        allTicks = []
        def addTicks(spacing, currdt, nexttime):
            ticks = []
            while currdt <= tmax:
                idx = data.clampIndex(data.findTimeIndex(timestamp(currdt)))
                if not any([idx in prevTicks[1] for prevTicks in allTicks]):
                    ticks.append(idx)
                currdt = nexttime(currdt)
            allTicks.append((spacing, ticks))

        rng = maxVal - minVal
        if rng > self.SPACING_MONTH * 6:
            addTicks(self.SPACING_YEAR,
                     dt.datetime(tmin.year, 1, 1),
                     lambda t: dt.datetime(t.year + 1, 1, 1))
        if rng < self.SPACING_YEAR * 2:
            addTicks(self.SPACING_MONTH,
                     dt.datetime(tmin.year, tmin.month, 1),
                     lambda t: dt.datetime(t.year + 1, 1, 1) if t.month == 12 \
                               else dt.datetime(t.year, t.month + 1, 1))
            if rng < self.SPACING_MONTH * 1:
                addTicks(self.SPACING_DAY,
                         dt.datetime(tmin.year, tmin.month, tmin.day),
                         lambda t: t + dt.timedelta(days=1))
                if rng < self.SPACING_DAY * 3:
                    addTicks(self.SPACING_HOUR,
                             dt.datetime(tmin.year, tmin.month, tmin.day, tmin.hour, 0, 0),
                             lambda t: t + dt.timedelta(hours=1))
            elif rng < self.SPACING_MONTH * 4:
                # Show week beginnings
                startingWeek = lambda t: dt.datetime(t.year, t.month, 7 * (int(math.ceil(t.day / 7.)) - 1) + 1)
                addTicks(self.SPACING_DAY,
                         startingWeek(tmin),
                         lambda t: startingWeek(t + dt.timedelta(days=7)))

        # Save before we modify for filter mode
        self.allTicks = {spacing:
                        [data.times[idx] for idx in ticks]
                        for spacing, ticks in allTicks}

        allTicks = [(spacing,
                    [data.plotTimes[idx] for idx in ticks])
                    for spacing, ticks in allTicks]

        return allTicks

# Shortern the volume numbers to fit more labels along the orderbook x-axis.
class SuffixVolumeAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        self.formatString = strVolume
        return [strVolume(x, 0) for x in values]

# Almost the same as pg.AxisItem except no standard form.
def tickStrings(self, values, scale, spacing):
    formatPlaces = max(0, math.ceil(-math.log10(spacing*scale)))
    self.formatString = lambda f, placesInc: ("{:,.%if}" % (formatPlaces+placesInc)).format(f)
    strings = [self.formatString(v, 0) for v in values]
    return strings
pg.AxisItem.tickStrings = tickStrings

def findYRange(vb, xRange, yColumns):
    data = vb.cg.data
    yColumns = wrapList(yColumns)
    if not data.count() or not yColumns:
        return None

    if data.exchange.filterGaps:
        idx = [data.unfilterIndex(x) for x in xRange]
    else:
        idx = [data.findTimeIndex(x) for x in xRange]

    if idx[1] > idx[0]:
        init = sys.float_info.max
        y_min = init
        y_max = -init
        idx = [data.clampIndex(i) for i in idx]
        for column in yColumns:
            column = column[idx[0]:idx[1]+1]
            y_min = min(y_min, column.min())
            y_max = max(y_max, column.max())

        if abs(y_min) != init and abs(y_max) != init:
            return y_min, y_max

    return None

def findXRange(vb, yRange):
    if vb.cg.data is None:
        return None

    y_min, y_max = yRange

    x_max = -sys.float_info.max
    ret = None # If there is nothing in view then dont change the viewRange.

    data = vb.cg.data
    for bidAsk in range(len(data.orderwall)):# len() can be zero if we have no data
        prices = data.orderwall[bidAsk][0]
        amounts = data.orderwall[bidAsk][1]

        for i in xrange(int(len(prices)*0.9)):
            price = prices[i]
            if price >= y_min and price <= y_max:
                amount = amounts[1 + i] # amounts starts with an extra 0
                x_max = max(x_max, amount)
                ret = [0, x_max * 1.2]

    return ret

class PricesViewBox(pg.ViewBox):
    def __init__(self, *args, **kwds):
        pg.ViewBox.__init__(self, *args, **kwds)

        def addAction(text, parentMenu, triggered, insertBefore=None):
            action = QtGui.QAction(text, parentMenu)
            action.triggered.connect(triggered)
            if insertBefore:
                parentMenu.insertAction(insertBefore, action)
            else:
                parentMenu.addAction(action)

        insertBefore = self.menu.actions()[0]
        insertBefore.setText('View &All')# Add keyboard shortcut to the View All option

        def addSeperator():
            action = QtGui.QAction('', self.menu)
            action.setSeparator(True)
            self.menu.insertAction(insertBefore, action)

        self.menuSearch = QtGui.QMenu('&Search Markets', self.menu)
        self.menu.insertMenu(insertBefore, self.menuSearch)

        action = QtGui.QAction('&All', self.menuSearch)
        action.triggered.connect(lambda _: self.cg.openSearchMenu(None))
        self.menuSearch.addAction(action)
        for exchange in sorted(exchanges.EXCHANGES, key=lambda e: e.name):
            if exchange.__class__.__base__ != exchanges.ExchangeWithSearch:
                continue
            addAction(exchange.nameWithShortcut, self.menuSearch,
                lambda _, e=exchange: self.cg.openSearchMenu(e))

        def openWatchlist():
            watchlist = self.cg.watchlist = watchlists.WatchlistWindow(self.cg)
            # Default position of watchlist at top right or parent chart window
            geo = watchlist.geometry()
            parentGeo = self.cg.window.geometry().topRight()
            geo.moveTo(parentGeo)
            watchlist.setGeometry(geo)
            watchlist.show()
            watchlist.startWatchlist()
        addAction('Open Watch&list', self.menu, openWatchlist, insertBefore)

        addSeperator()

        # Add interval submenu to context menu
        timeframeMenu = QtGui.QMenu('&Timeframe', self.menu)
        # Create the interval options depending on what chart group we are looking at.
        def onTimeframeMenuShow():
            timeframeMenu.clear()
            data = self.cg.data
            if not data.count():
                return
            intervals = data.exchange.intervals
            sortedBySeconds = sorted(intervals, key=lambda i: intervalSeconds(i))
            lastIntervalLetter = ''
            for timeframe in sortedBySeconds:
                interval = timeframe
                if interval == '5m':
                    interval = '&5m'
                elif interval[-1] != lastIntervalLetter:
                    lastIntervalLetter = interval[-1]
                    interval = interval[:-1] + '&' + lastIntervalLetter
                addAction(interval, timeframeMenu,
                    lambda _, timeframe=timeframe: self.cg.changeTimeInterval(timeframe))
        timeframeMenu.aboutToShow.connect(onTimeframeMenuShow)
        self.menu.insertMenu(insertBefore, timeframeMenu)

        # Add links submenu to context menu
        linksMenu = QtGui.QMenu('Links', self.menu)
        def onLinksMenuShow():
            linksMenu.clear()
            for desc, url in sorted(self.cg.links.items()):
                addAction(desc, linksMenu,
                    lambda _, url=url: QtGui.QDesktopServices.openUrl(QtCore.QUrl(url)))
        linksMenu.aboutToShow.connect(onLinksMenuShow)
        self.menu.insertMenu(insertBefore, linksMenu)

        # Add resample submenu to context menu
        resampleMenu = QtGui.QMenu('R&esample Time', self.menu)
        def onResampleMenuShow():
            resampleMenu.clear()
            data = self.cg.data
            if not data.count():
                return

            resampleTable = {'4&h': ['m', 'h'], '1&w': ['d'], '1&M': ['d'], '1&d': ['h']}
            for toTime, fromTime in resampleTable.items():
                if data.timeframe[-1] in fromTime:
                    addAction(toTime, resampleMenu,
                        lambda _, toTime=toTime: self.cg.resampleData(toTime.replace('&', '')))
        resampleMenu.aboutToShow.connect(onResampleMenuShow)
        self.menu.insertMenu(insertBefore, resampleMenu)

        optionsMenu = QtGui.QMenu('Chart &Options', self.menu)
        self.menu.insertMenu(insertBefore, optionsMenu)

        def addToggle(showMember, text):
            def toggle(cg, showMember):
                showVar = 'show' + showMember
                value = not getattr(cg, showVar)
                setattr(cg, showVar, value)
                gSettings.setValue(showVar, value)
                if value and showMember == 'Orderwall':
                    # Toggling the orderwall requires downloading the orderbook
                    cg.downloadNewData()
                else:
                    cg.reAddPlotItems()

            addAction(text, optionsMenu,
                lambda: toggle(self.cg, showMember))
        for var, desc in SHOW_OPTIONS:
            addToggle(var, desc)

        addAction('&Refresh Chart', self.menu,
            lambda: self.cg.downloadNewData(), insertBefore)

        addSeperator()

        def newChart(cg, incRowOrCol):
            newCoord = [cg.coord[0], cg.coord[1]]
            newCoord[incRowOrCol] += 1
            cg.window.setChartAt(None, newCoord)
        addAction('New Chart &Horizontally', self.menu,
            lambda: newChart(self.cg, COL), insertBefore)
        addAction('New Chart &Vertically', self.menu,
            lambda: newChart(self.cg, ROW), insertBefore)

        addAction('Co&py Chart', self.menu,
            lambda: self.cg.copyMarketToAdjacentChart(None), insertBefore)
        addAction('&Close Chart', self.menu,
            lambda: self.cg.window.removeChart(self.cg), insertBefore)

        addSeperator()

    def mouseDragEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier:
            self.setMouseMode(self.RectMode)
        else:
            self.setMouseMode(self.PanMode)

        pg.ViewBox.mouseDragEvent(self, ev)

    def setRange(self, rect=None, xRange=None, yRange=None, padding=None, update=True, disableAutoRange=True):
        cg = self.cg
        data = cg.data

        if type(yRange) == list:
            pass
        elif data.count() and not (app.keyboardModifiers() & Qt.ShiftModifier):
            viewRange = self.state['viewRange']
            xr = [rect.left(), rect.right()] if rect else viewRange[0]

            if data.isOHLC:
                columns = [data.high, data.low]
            else:
                columns = [data.close]

            yRange = findYRange(self, xr, columns) or viewRange[1]

        padding = 0.
        pg.ViewBox.setRange(self, rect, xRange, yRange, padding, update, disableAutoRange)

class ExtraViewBox(pg.ViewBox):# Also used for Volume
    def __init__(self, clampToZero=True, *args, **kwds):
        pg.ViewBox.__init__(self, *args, **kwds)
        self.clampToZero = clampToZero
        self.yColumns = []

    def setRange(self, rect=None, xRange=None, yRange=None, padding=None, update=True, disableAutoRange=True):
        viewRange = self.state['viewRange']
        xr = [rect.left(), rect.right()] if rect else xRange or viewRange[0]

        yRange = findYRange(self, xr, self.yColumns) or viewRange[1]
        if self.clampToZero:
            EPSILON = 1e-10 # So we always see symbols on the x-axis on pulse chart.
            yRange = (min(-EPSILON, yRange[0]), max(EPSILON, yRange[1]))# Lock bottom of volume chart to the horizontal axis

        padding = 0.
        pg.ViewBox.setRange(self, rect, xRange, yRange, padding, update, disableAutoRange)

class OrderbookViewBox(pg.ViewBox):
    def __init__(self, *args, **kwds):
        pg.ViewBox.__init__(self, *args, **kwds)

    def setRange(self, rect=None, xRange=None, yRange=None, padding=None, update=True, disableAutoRange=True):
        viewRange = self.state['viewRange']
        yr = [rect.bottom(), rect.top()] if rect else yRange or viewRange[1]

        xRange = findXRange(self, yr) or viewRange[0]
        xRange = (xRange[1], 0)# Lock right of orderbook to the vertical axis

        pg.ViewBox.setRange(self, rect, xRange, yRange, padding, update, disableAutoRange)

class CandlestickItem(pg.GraphicsObject):
    def __init__(self, picture):
        pg.GraphicsObject.__init__(self)
        self.picture = picture

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        rect = QtCore.QRectF(self.picture.boundingRect())
        return rect

def legendItemName(legend, idx):
    return legend.items[idx][1].text
def clearLegend(legend):
    while legend.items:
        legend.removeItem(legendItemName(legend, 0))
def zeroLayoutMargins(layout):
    layout.setSpacing(0)
    layout.setContentsMargins(0, 0, 0, 0)

class ChartGroup():
    def __init__(self, window):
        self.window = window
        self.locateGroup = None
        self.loadDefaultChartSettings()

        self.widget = pg.GraphicsLayoutWidget()
        zeroLayoutMargins(self.widget.ci)

        self.plotTypes = [Struct(plots=[]) for i in range(PLOT_COUNT)]
        self.links = {}

    def loadDefaultChartSettings(self):
        defaultShow = [
            'Volume',
            'BB',
            'KC',
            'Pulse',
            'TrendBars'
        ]
        for var, desc in SHOW_OPTIONS:
            showVar = 'show' + var
            value = gSettings.value(showVar)
            if value.isNull():
                # Initialize the default saved setting
                value = var in defaultShow
                gSettings.setValue(showVar, value)
            else:
                value = value.toBool()
            setattr(self, showVar, value)

    def mainPlot(self): return self.plotTypes[PLOT_MAIN].plots[0]

    def keyPressEvent(self, evt):
        # Reuse shortcuts in right click menu
        vb = self.mainPlot().vb
        pos = QtGui.QCursor.pos()
        for action in vb.menu.actions():
            text = str(action.text())
            idx = text.find('&')
            if idx != -1 and evt.key() == ord(text[idx+1].upper()):
                menu = action.menu()
                if menu:
                    menu.popup(pos)
                else:
                    action.trigger()
                break

    def changeTimeInterval(self, timeframe):
        self.market.timeframe = timeframe
        self.downloadNewData()
    def resampleData(self, timeframe):
        data = self.data.resampleNew(timeframe)
        self.assignData(data)

    def changeMarket(self, marketStructOrData):
        if type(marketStructOrData) == ChartData:
            data = marketStructOrData
            self.market = data.market()
            self.assignData(data)
        else:
            market = marketStructOrData
            if market and not market.timeframe:
                defaultTimeframe = market.exchange.defaultTimeframe
                # Default to timeframe that we are already showing.
                for prevRow in range(self.coord[ROW]):
                    prevCg = self.window.charts.get((prevRow, self.coord[COL]))
                    if prevCg.market:
                        defaultTimeframe = prevCg.market.timeframe
                        break
                market.timeframe = defaultTimeframe
            self.market = market
            self.downloadNewData()

        self.window.onMarketsChanged(self)

    def copyMarketToAdjacentChart(self, timeframe='h'):
        if not self.market:
            return
        coord = (self.coord[0], self.coord[1] + 1)
        if timeframe:
            market = self.market.copy()
            market.timeframe = timeframe
            self.window.setChartAt(market, coord)
        else:
            self.window.setChartAt(self.data, coord) # Display data again without re-downloading

    def downloadNewData(self):
        data = ChartData(self.market)# Blank data object
        if self.market:# Check for empty chart
            data.downloadAndParse(getOrders=self.showOrderwall)

            if data.exchange.appendMinuteData and data.timeframe[1] in ['h', 'd']:
                market = self.market.copy()
                market.timeframe = 'm'
                minuteDataToCopy = ChartData(market)
                minuteDataToCopy.downloadAndParse()
                data.appendMinuteData(minuteDataToCopy)

        self.assignData(data)

    def forceRecalcRanges(self):
        # Trigger all the y-ranges to be recalculated.
        # Needed when changing the timeframe of an existing chart.
        vb = self.mainPlot().vb
        xRange = vb.state['viewRange'][0]
        vb.setRange(xRange=[0, 1])
        vb.setRange(xRange=xRange)

    def assignData(self, data):
        data.calcIndicatorsMakePlots()
        self.data = data
        self.reAddPlotItems()
        self.forceRecalcRanges()

    def openSearchMenu(self, exchange):
        def onChooseSearchResult(result):
            self.changeMarket(parseToMarketStruct(result, exchange))
            if exchange:
                exchange.symbols[result] = None
                exchange.saveSymbols()

        def onSearchStringAll(searchString):
            symbols = []
            # Build list of all exchange symbols
            for exchange in exchanges.EXCHANGES:
                for s in exchange.symbols:
                    symbols.append(str(s) + ' / ' + exchange.name)

            return sorted(symbols)

        onSearchString = onSearchStringAll
        if exchange:
            onSearchString = exchange.onSearchString
            exchange.onSearchOpen()# e.g. Login to IG Index

        self.finder = symbolfinder.SymbolFinder(self.widget, onSearchString, onChooseSearchResult)
        # QLineEdit inherits its background color from the mainwindow, which is black making the text unreadable.
        self.finder.edit.setPalette(app.style().standardPalette())
        self.finder.show()

    def clearLocate(self):
        if self.locateGroup is None:
            return
        self.mainPlot().vb.scene().removeItem(self.locateGroup)
        self.locateGroup = None

    def setLinesAndFillLegend(self, globalMousePos, mainPlotViewPos):
        mousePos = self.widget.mapFromGlobal(globalMousePos)
        mainPlot = self.mainPlot()

        idxInRange = False
        data = self.data
        time = mainPlotViewPos.x()
        if data.count():

            # Add on half an interval so we transition at the bar edges and not in the middle.
            #time += data.timeInterval/2
            # Find data to put in OHLC legend.
            if data.exchange.filterGaps:
                idx = data.unfilterIndex(time)
            else:
                idx = data.findTimeIndex(time) - 1

            idx = data.clampIndex(idx)# Make it always in range
            idxInRange = idx >= 0 and idx < data.count()

        datetimeStr=None
        if idxInRange:
            time = data.plotTimes[idx]

            formatDatetime = lambda datetime: fromtimestamp(datetime).strftime('%d-%m-%Y %H:%M:%S')
            datetimeStr = formatDatetime(data.times[idx])

            nv = lambda name, strValue: '<tr><td>' + (name + ':').ljust(8) + '</td><td><b>' + strValue.ljust(8) + '</b></td></tr>'
            formatPrice = lambda price: '{:,.8f}'.format(price).rstrip('0').ljust(10)
            if data.isOHLC:
                open, high, low, close, volume = data.getOHLCV(idx)
                label = nv('Open', formatPrice(open)) + \
                        nv('High', formatPrice(high)) + \
                        nv('Low', formatPrice(low)) + \
                        nv('Close', '<font color="%s">%s</font>' % (OPEN_CLOSE_COLOR[close >= open], formatPrice(close))) + \
                        (nv('Volume', strVolume(volume)) if data.hasVolume else '')
            else:
                label = nv('Close', formatPrice(data.close[idx]))
            label = datetimeStr + '<table>' + label + '</table>'

            legend = mainPlot.legend
            if legend.items:
                lastText = legendItemName(legend, -1)
                if lastText.find('<table>') != -1:
                    legend.removeItem(lastText)
            legend.addItem(Struct(opts={'pen':(0,0,0,0)}), label)
            legend.setGeometry(0, 0, legend.width()-25, legend.height())# Undo ever-increasing width in LegendItem.updateSize()

        overThis = [self.coord[i] == self.window.mouseOverChartGroup.coord[i] for i in range(2)]

        PEN_CROSSHAIR = '999999ff'
        for i,typeList in enumerate(self.plotTypes):
            for plt in typeList.plots[:typeList.usedCount]:
                vbRect = plt.vb.viewRect()
                try:
                    plotViewPos = plt.vb.mapSceneToView(mousePos)
                except:# LinAlgError: Singular matrix
                    continue

                lineLabel = False
                axisPlot = None
                plotX = time
                plotY = mainPlotViewPos.y()
                if i == PLOT_MAIN:
                    lineLabel = datetimeStr
                elif i == PLOT_ORDERWALL:
                    plotX = plotViewPos.x()
                    lineLabel = True
                    axisPlot = mainPlot
                else:
                    plotY = plotViewPos.y()
                plt.createLine('lineH', plotY, PEN_CROSSHAIR, lineLabel=True, visible=overThis[ROW], angle=0, labelPos=vbRect.right(), axisPlot=axisPlot)
                plt.createLine('lineV', plotX, PEN_CROSSHAIR, lineLabel, visible=True, angle=90, labelPos=vbRect.top())

    def createPlot(self, plotType):
        typeList = self.plotTypes[plotType]
        if typeList.usedCount < len(typeList.plots):
            ret = typeList.plots[typeList.usedCount]
            typeList.usedCount += 1
            return ret

        vb = { # Viewboxes will be replaced by PlotItems
            PLOT_MAIN: PricesViewBox,
            PLOT_VOLUME: ExtraViewBox,
            PLOT_ORDERWALL: OrderbookViewBox,
            PLOT_EXTRA_TA: ExtraViewBox,
        }[plotType]()
        vb.cg = self # Needed for findYRange

        # Hide Legend if mouse is not over this plot
        vb.setAcceptHoverEvents(True)
        def hoverEnterEvent(ev):
            legend = getattr(plt, 'legend', None)
            if legend: plt.legend.setVisible(True)
        def hoverLeaveEvent(ev):
            legend = getattr(plt, 'legend', None)
            if legend: plt.legend.setVisible(False)
        vb.hoverEnterEvent = hoverEnterEvent
        vb.hoverLeaveEvent = hoverLeaveEvent

        if plotType == PLOT_ORDERWALL:
            axisItems = {'bottom': SuffixVolumeAxis(orientation='bottom')}
        elif plotType == PLOT_MAIN:
            dateAxis = DateAxis(orientation='bottom')
            dateAxis.cg = self
            axisItems = {'bottom': dateAxis}
        elif plotType == PLOT_VOLUME:
            axisItems = {'right': SuffixVolumeAxis(orientation='right')}
        else:
            axisItems = {}

        plt = pg.PlotItem(viewBox=vb,
                          axisItems=axisItems)

        plt.showAxis('left', False)
        plt.showGrid(x=True, y=True, alpha=0.3)
        if plotType == PLOT_ORDERWALL:
            plt.setTitle('Orderbook')
            plt.showAxis('right', False)
            plt.setYLink(self.mainPlot())
        else:
            plt.showAxis('right')
            if plotType != PLOT_MAIN:
                plt.showAxis('bottom', False)

        if plotType in [PLOT_MAIN, PLOT_EXTRA_TA]:
            plt.addLegend(offset=(5,5))
            plt.legend.setVisible(False)
        if plotType in [PLOT_VOLUME, PLOT_EXTRA_TA]:
            plt.setXLink(self.mainPlot())

        plt.lineItems = {}
        def createLine(plt, name, linePos, pen, lineLabel=None, visible=True, angle=0, labelPos=None, **kwds):
            item = plt.lineItems.get(name)
            if not item:
                item = pg.InfiniteLine(angle=angle, movable=False, pen=pen)
                plt.lineItems[name] = item
                item.setZValue(-1000000)# Send it to the back
                item.setPen(pen)
                plt.vb.addItem(item, ignoreBounds=True)
            item.setPos(linePos)
            item.setVisible(visible)

            if not lineLabel:
                return

            if type(lineLabel) == str:
                text = lineLabel
            else:
                axisPlot = kwds.get('axisPlot') or plt
                axis = axisPlot.axes['bottom' if angle == 90 else 'right']['item']
                formatString = getattr(axis, 'formatString', None)
                if not formatString:
                    return
                # Using almost the same number of decimal places as the ticks in the axis.
                text = formatString(linePos, 1)

            if angle == 90:
                linePos, labelPos = labelPos, linePos
                anchor = (0,1)
            else:
                anchor = (1,1)

            name += '_text'
            item = plt.lineItems.get(name)
            if not item:
                item = pg.TextItem(anchor=anchor)
                plt.lineItems[name] = item
                item.setColor(pen)
                item.setZValue(1000)
                plt.vb.addItem(item, ignoreBounds=True)
            item.setText(text)

            item.setPos(labelPos, linePos)
            item.setVisible(visible)

        plt.createLine = types.MethodType(createLine, plt)

        typeList.plots.append(plt)
        typeList.usedCount = len(typeList.plots)
        return plt

    def reAddPlotItems(self):
        # Clear plot items
        for i,typeList in enumerate(self.plotTypes):
            typeList.usedCount = 0
            for plot in typeList.plots:
                plot.clear()
                if i in [PLOT_MAIN, PLOT_EXTRA_TA]:
                    clearLegend(plot.legend)
        self.widget.clear()

        data = self.data
        title = ''
        if data.count():
            title = data.exchange.name + ': ' +\
                (data.description + ' | ' if data.description else '') +\
                data.symbolKey + ' ' + data.timeframe

        mainPlot = self.createPlot(PLOT_MAIN)
        mainPlot.titleLabel.updateMin = lambda: None # Override the LabelItem.setText() setting a minimum width, which prevents chart from scaling.
        mainPlot.setTitle(title)

        row = 0
        self.widget.addItem(mainPlot, row, col=0)
        row += 1

        # Has to be done after items are added to widget
        layout = self.widget.ci.layout
        STRETCH_FACTOR = 4
        layout.setRowStretchFactor(0, STRETCH_FACTOR)
        layout.setColumnStretchFactor(0, STRETCH_FACTOR)

        if not data.count():
            return

        if data.isOHLC:
            mainPlot.addItem(CandlestickItem(data.createCandlestick(False, self.showTrendBars)))
            if self.showVolume and data.hasVolume:
                plt = self.createPlot(PLOT_VOLUME)
                plt.vb.yColumns = data.volume
                plt.addItem(CandlestickItem(data.createCandlestick(True, self.showTrendBars)))

                self.widget.addItem(plt, row, col=0)
                row += 1
        else:
            mainPlot.plot(data.plotTimes, data.close.tolist()[:data.count()], pen='ffff00ff')

        if self.showOrderwall and data.orderwall:
            plt = self.createPlot(PLOT_ORDERWALL)
            for bidAsk in range(len(data.orderwall)):# len() can be zero if we have no data
                prices = data.orderwall[bidAsk][0]
                amounts = data.orderwall[bidAsk][1]
                plt.plot(
                    amounts, prices, stepMode=True, fillLevel=prices[-1],
                    brush=[(0,255,0,100),(255,0,0,100)][bidAsk])
            self.widget.addItem(plt, row=0, col=1, rowspan=10)

        # Add plot items
        for s in sorted(TA_LIST):
            ta = TA_LIST[s]
            if getattr(self, 'show' + s):
                lines = data.getTA(s)
                if lines[0].extraTA:
                    plt = self.createPlot(PLOT_EXTRA_TA)
                    plt.vb.yColumns = lines[0].yColumns
                    self.widget.addItem(plt, row, col=0)
                    row += 1
                    addToPlot = plt
                else:
                    addToPlot = mainPlot
                for taLine in lines:
                    opts = taLine.__dict__.copy()
                    for col, yColumn in enumerate(wrapList(taLine.yColumns)):
                        floatY = yColumn.tolist()[:data.count()]
                        if hasattr(taLine, 'x'):
                            addToPlot.addItem(pg.PlotDataItem(y=floatY, **opts))
                        elif taLine.barGraph:
                            opts.update(x=data.plotTimes,
                                        width=data.timeInterval)
                            opts['width'] /= 1.
                            addToPlot.addItem(pg.BarGraphItem(height=floatY, **opts))
                        else:
                            xy = (data.plotTimes, floatY)
                            addToPlot.addItem(pg.PlotDataItem(*xy, **opts))
                        if col and hasattr(addToPlot, 'legend'):
                            # Only allow each indicator to have one line in the legend.
                            addToPlot.legend.removeItem(taLine.name)

        self.position = None
        self.links = {}
        data.exchange.onChartLoad(self)

        position = self.position
        if position:
            openTime = data.plotTimes[data.clampIndex(data.findTimeIndex(position['createdDate']))]
            #pen={'color': (255, 0, 0), 'width': 2}
            if position['stopLevel'] != None:
                mainPlot.createLine('stopLevel', position['stopLevel'], 'FF0000', lineLabel=True, labelPos=openTime)

            if position['limitLevel'] != None:
                mainPlot.createLine('limitLevel', position['limitLevel'], '0000FF', lineLabel=True, labelPos=openTime)

            if position['openLevel'] != None:
                mainPlot.createLine('openLevel', position['openLevel'], '00FFFF', lineLabel=True, labelPos=openTime)

                arrow = pg.ArrowItem(pos=(
                                        openTime,
                                        position['openLevel']),
                                     angle=90,
                                     pen=(255, 255, 255),
                                     brush=(0, 0, 0, 0),
                                     size=30)
                mainPlot.addItem(arrow)

allWindows = []
class ChartWindow(QtGui.QMainWindow):
    def __init__(self, marketPairList=[]):
        super(ChartWindow, self).__init__()
        self.setWindowTitle('qmarket')
        cw = QtGui.QWidget()
        self.setCentralWidget(cw)
        self.layout = QtGui.QGridLayout()
        zeroLayoutMargins(self.layout)
        cw.setLayout(self.layout)
        self.restoreGeometry(gSettings.value('geometry').toByteArray())
        QtGui.QShortcut(QtGui.QKeySequence('Q'), self, app.closeAllWindows)
        QtGui.QShortcut(QtGui.QKeySequence('W'), self, self.close)
        QtGui.QShortcut(QtGui.QKeySequence('N'), self, lambda: ChartWindow().show())
        QtGui.QShortcut(QtGui.QKeySequence('Space'), self, lambda: self.mouseOverChartGroup.mainPlot().vb.menu.popup(QtGui.QCursor.pos()))

        # Set background palette to black
        pal = self.palette()
        pal.setColor(QtGui.QPalette.Window, Qt.black)
        self.setPalette(pal)

        self.closeEvent = self.closeEvent
        self.mouseOverChartGroup = None

        self.charts = MultiKeyDict()
        allWindows.append(self)

        coord = [0, 0]
        if marketPairList:
            if type(marketPairList) is tuple:
                marketPairList = [marketPairList]
            for marketPair in marketPairList:
                self.setChartAt(marketPair, coord)
                coord[ROW] += 1
        else:
            self.setChartAt(None, coord)

    def closeEvent(self, evt):
        if not self.isMaximized():# De-maximizing from a saved state causes the window to be tiny.
            gSettings.setValue('geometry', self.saveGeometry())

    def keyPressEvent(self, evt):
        if self.mouseOverChartGroup:
            self.mouseOverChartGroup.keyPressEvent(evt)

    def focusOnChartGroup(self, cg):
        self.mouseOverChartGroup = cg

        globalMousePos = QtGui.QCursor.pos()
        mainPlotViewPos = cg.mainPlot().vb.mapSceneToView(cg.widget.mapFromGlobal(globalMousePos))

        charts = self.charts
        for row in charts:
            for col in charts[row]:
                charts.get([row, col]).setLinesAndFillLegend(globalMousePos, mainPlotViewPos)

    def onMarketsChanged(self, changedCG):
        changedPlot = changedCG.mainPlot()
        if changedCG.coord[1] == 0:
            changedPlot.setRange(yRange=(0, 0))# Will be overriden by findYRange

        titleStr = ''
        for rowOrCol in range(2):
            chart0 = None
            coord = [c for c in changedCG.coord]
            for c in range(changedCG.coord[rowOrCol]):
                coord[rowOrCol] = c
                chart0 = self.charts.get(coord)
                if chart0:
                    # Set the window title to the most top-left chart, which comes first
                    titleStr = titleStr or chart0.mainPlot().titleLabel.text
                    break

            if not chart0 or changedCG is chart0:
                continue

            chart0 = chart0.mainPlot()
            changedPlot.setXLink(chart0)
            if rowOrCol == COL:
                changedPlot.setYLink(chart0)

    def setChartAt(self, market, coord):
        cg = self.charts.get(coord)
        if not cg:
            cg = ChartGroup(self)
            cg.coord = (coord[0], coord[1])
            self.charts.set(coord, cg)
            self.layout.addWidget(cg.widget, *cg.coord)
            cg.widget.scene().sigMouseMoved.connect(lambda _: self.focusOnChartGroup(cg))
            self.mouseOverChartGroup = cg
            self.layout.setColumnStretch(coord[1], 1 if coord[1] else 2)

        cg.changeMarket(market)

    def removeChart(self, cg):
        self.layout.setColumnStretch(cg.coord[1], 0)
        self.layout.removeWidget(cg.widget)
        cg.widget.deleteLater()
        self.charts.unset(cg.coord)

def show():
    QtGui.QApplication.instance().exec_()

if __name__ == '__main__':

    defaults = [0, 0]
    for i in range(min(len(sys.argv)-1, len(defaults))):
        try:
            defaults[i] = int(sys.argv[1 + i])
        except: pass

    marketsToAdd = []
    sp_space = sys.argv[1:]
    for arg in sp_space:
        market = parseToMarketStruct(arg)
        marketsToAdd.append(market)
    ChartWindow(marketsToAdd).show()

    show()
