import os, sys, requests, websocket, json, bisect
import numpy as np, pandas as pd
import pyqtgraph as pg
from pyqtgraph import QtCore, QtGui
from utils import *
import exchanges

OPEN = 0
HIGH = 1
LOW = 2
CLOSE = 3
VOLUME = 4

TA_LIST = dict(
    MA1 = [
        Struct(length=8, pen={'color': (0xfd, 0xbf, 0x6f), 'width': 2}),
        Struct(length=21,  pen={'color': (0xa6, 0xce, 0xe3), 'width': 2}),
    ],
    MA2 = [
        Struct(length=50, pen={'color': (0xfd, 0xbf, 0x6f), 'width': 2}),
        Struct(length=200,  pen={'color': (0xa6, 0xce, 0xe3), 'width': 2}),
    ],
    BB = Struct(length=20, stdDevMult=2., pen={'color': (0, 255, 255), 'width': 1}),
    BBOver = Struct(pen={'color': (255, 255, 255), 'width': 1}),
    KC = Struct(length=20, hlRangeMult=1.5, pen={'color': (255, 0, 127), 'width': 1}),
    ADX = Struct(length=20,
        penADX={'color': (255, 255, 0), 'width': 2},
        penPlusDI={'color': (0, 255, 0), 'width': 1},
        penMinusDI={'color': (255, 0, 0), 'width': 1}),
    Pulse=Struct(momentumN=12, momentumMA=5),
)

def floatToIndex(floatIdx):
    return int(round(floatIdx))

def parseToMarketStruct(string, exchangeAlreadyKnown=None):
    # Cut off any preceeding description, eg: Advanced Micro Devices, Inc. | NASDAQ:AMD
    sp = [s.strip() for s in string.split('|')]

    description = sp[0] if len(sp) > 1 else ''
    string = sp[-1]

    sp = [s.strip() for s in string.split('/')]
    if exchangeAlreadyKnown:
        exchange = exchangeAlreadyKnown
    else:
        exchange = exchanges.findExchange(sp.pop())

    # Symbol itself might have slashes, eg: BTS/BTC / Poloniex
    symbolKey = '/'.join(sp)

    # In case of incomplete symbol name
    symbolKey = exchange.findSymbol(symbolKey)

    # Cut off the description again, as findSymbol() may have added it back.
    symbolKey = (symbolKey.split('|')[-1]).strip()

    return Struct(exchange=exchange,
                  symbolKey=symbolKey,
                  description=description,
                  timeframe='')

class ChartData(pd.DataFrame):
    # Static because this is set a few levels above where downloadAndParse is called.
    cacheSeconds = 0

    def __init__(self, market, existingDf=None):
        if existingDf is not None:
            if existingDf is not self:# Watchlist calls __init__ explicitly to fix the subclass.
                super(ChartData, self).__init__(existingDf)
        else:
            super(ChartData, self).__init__(columns=['times', 'open', 'high', 'low', 'close', 'volume'])

        self.orderwall = []
        self.isOHLC = False
        self.hasVolume = False

        if not market:
            return
        self.__dict__.update(market.__dict__)

        if existingDf is not None and self.count():
            self.onDataChange()# We already have a filled ChartData object
            #return # Dont override the interval key - WHY?

        if len(self.timeframe) == 1:
            # marketTuple can skip multiple on interval, eg 'h'
            lowest = sys.maxint
            for timeframe in self.exchange.intervals:
                if timeframe[-1] != self.timeframe:
                    continue
                multiple = int(timeframe[:-1])
                lowest = min(lowest, multiple)
            if lowest != sys.maxint:
                self.timeframe = str(lowest) + self.timeframe

    def npZeros(self, cols=1):
        return np.zeros((self.count(), cols))

    def setOHLC(self, tohlcv):
        if not tohlcv:
            return

        tohlcv = zip(*tohlcv)
        for c, col in enumerate(self.columns):
            self[col] = tohlcv[c]

        # Some entries on Google are all 0 except for the close
        for col in ['open', 'high', 'low']:
            self.loc[self[col]==0., col] = self.close

        # Yahoo often has currencies upside down
        ohlcCols = self[['open', 'high', 'low', 'close']]
        self.high = ohlcCols.max(axis=1)
        self.low = ohlcCols.min(axis=1)

        self.onDataChange()

    def market(self):
        return Struct(exchange=self.exchange,
                      symbolKey=self.symbolKey,
                      description=self.description,
                      timeframe=self.timeframe)
    def resampleNew(self, timeframe):# 'D' / 'H'
        # Convert the integer timestamps in the index to a DatetimeIndex
        # This interprets the integers as seconds since the Epoch.
        self.index = pd.to_datetime(self.times, unit='s')

        ohlc_dict = dict(times='first', open='first', high='max', low='min', close='last', volume='sum')
        df = self.resample(timeframe.upper(), how=ohlc_dict, closed='right', label='right')
        df = df[~df.times.isnull()]# Remove rows with NANs

        df.__class__ = ChartData # Saves a data copy.
        market = self.market()
        market.timeframe = timeframe
        df.__init__(market, df)
        return df

    def appendMinuteData(self, minuteDataToCopy):
        dtCompare = {'d': lambda t: t.date(),
                     'h': lambda t: (t.date(), t.hour)}[self.timeframe[1]]

        dtNow = now()
        #resampled = self.resampleNew(self.timeframe[1].upper())#FIXME
        ohlcv = minuteDataToCopy.calcOHLCForPeriod(dtNow, dtCompare)
        if ohlcv[LOW] == sys.float_info.max:
            return

        tohlcv = [timestamp(dtNow)] + ohlcv
        if self.count() and dtCompare(fromtimestamp(self.times.iloc[-1])) == dtCompare(dtNow):
            # Replace last OHLC entry
            self.iloc[-1] = tohlcv
        else:
            # Append OHLC entry
            self.loc[self.count()] = tohlcv

        self.onDataChange()

    def onDataChange(self):
        if self.exchange.filterGaps:
            avgInterval = (self.times.iloc[-1] - self.times[0]) / self.count()
            self.timeInterval = avgInterval
            # Yahoo has weekend gaps etc
            self.plotTimes = [self.times[0] + avgInterval*i for i in xrange(self.count())]
        else:
            # Calculate modal average interval, in case data has gaps
            self.timeInterval = float((self.times - self.times.shift(1)).mode())
            self.plotTimes = self.times

        self.isOHLC = self.high.max() != 0.
        self.hasVolume = self.volume.max() > 0.

        self.candleStickPictures = [[None]*2, [None]*2]
        self.calculatedIndicators = False

    def getOHLCV(self, e):
        return self.open[e], self.high[e], self.low[e], self.close[e], self.volume[e]

    def count(self):
        return len(self.times)

    def clampIndex(self, floatIdx, inclusive=True):
        idx = floatToIndex(floatIdx)
        idx = max(min(idx, self.count()), 0)
        if inclusive and idx == self.count():
            idx -= 1
        return idx

    def clampIndexTime(self, idx):
        return self.times[self.clampIndex(idx)]

    def findTimeIndex(self, time):
        return self.times.searchsorted(time)[0]

    def unfilterIndex(self, time):
        ret = int((time - self.times[0]) / self.timeInterval)
        return ret

    def calcOHLCForPeriod(self, dtForDay, dtCompare):
        ohlcv = [0.]*(VOLUME+1)
        ohlcv[HIGH] = -sys.float_info.max
        ohlcv[LOW] = sys.float_info.max

        for e in xrange(self.count()):
            time = fromtimestamp(self.times[e])
            if dtCompare(time) != dtCompare(dtForDay):
                continue

            if ohlcv[OPEN] == 0.:
                ohlcv[OPEN] = self.open[e]
            ohlcv[CLOSE] = self.close[e]
            ohlcv[HIGH] = max(ohlcv[HIGH], self.high[e])
            ohlcv[LOW] = min(ohlcv[LOW], self.low[e])
            ohlcv[VOLUME] += self.volume[e]

        return ohlcv

    def calcPulse(self, momentumN, momentumMA, **unused):
        retMomentum = self.npZeros()
        pensMomentum = []
        bbInside, bbOutside = [], []

        self.squeezeDuration = 0
        self.stepsSinceSqueeze = 0
        self.squeezeState = 'Ended'

        # Decreasing width is an early sign the momentum will soon slowdown.
        bbWidth = self.bbUpper - self.bbLower
        kcWidth = self.kcUpper - self.kcLower
        bbSqueeze = (self.bbUpper < self.kcUpper) & (self.bbLower > self.kcLower)

        if 0:
            # Show rate of bollinger's expansion
            bbWidthLast = bbWidth.shift(1)
            momentum = (bbWidth - bbWidthLast) / bbWidthLast
            momentum[0:20] = 0.# Can be wild before the bollinger is finished
            momentum[momentum < 0.] = 0.
            momentum[self.close < self.bbMean] *= -1.
        else:
            # Show real momentum
            momentum = self.close.diff(momentumN)
            momentum[momentum.isnull()] = 0.
            momentum = pd.rolling_mean(momentum, momentumMA)

        width = bbWidth / kcWidth
        slowdownPrediction = abs(width.shift(1)) > abs(width)

        prevWidth = 0.
        for e in xrange(self.count()):
            time = self.plotTimes[e]

            # Dots for BB squeeze
            if bbSqueeze[e]:
                self.squeezeState = 'Squeeze'

                if self.stepsSinceSqueeze:
                    self.stepsSinceSqueeze = 0
                    self.squeezeDuration = 0

                self.squeezeDuration += 1
                bbInside.append(time)
            else:
                if self.squeezeState == 'Squeeze':
                    self.squeezeState = 'Fired'

                self.stepsSinceSqueeze += 1
                bbOutside.append(time)

            # Momentum histogram with faded colors when we are predicting a slowdown.
            pen = [0,255,0] if momentum[e] >= 0. else [255,0,0]
            if slowdownPrediction[e]:
                pen = [c/2 for c in pen]
                if self.squeezeState == 'Fired':
                    self.squeezeState = 'Ended'
            pensMomentum.append(pen)

        if self.squeezeState != 'Squeeze':
            self.squeezeState += ' (%i)' % self.stepsSinceSqueeze

        return momentum, pensMomentum, bbInside, bbOutside

    def calcADX(self, length, **unused):

        trueRange = pd.DataFrame()
        trueRange[0] = self.high - self.low
        trueRange[1] = abs(self.high - self.close.shift(1))
        trueRange[2] = abs(self.low - self.close.shift(1))
        trueRange = trueRange.max(axis=1)

        upMove = self.high - self.high.shift(1)
        downMove = self.low.shift(1) - self.low

        plusDM = np.where((upMove > downMove) & (upMove > 0.), upMove, 0.)
        minusDM = np.where((downMove > upMove) & (downMove > 0.), downMove, 0.)

        kwds = dict(window=length, min_periods=0)
        atr = pd.rolling_mean(trueRange, **kwds)
        plusDI = 100. * pd.rolling_mean(plusDM, **kwds) / atr
        minusDI = 100. * pd.rolling_mean(minusDM, **kwds) / atr

        finalCalc = abs((plusDI - minusDI) / (plusDI + minusDI))
        finalCalc[0] = 0.
        adx = 100. * pd.rolling_mean(finalCalc, **kwds)

        ret = pd.DataFrame()
        ret[0] = adx
        ret[1] = plusDI
        ret[2] = minusDI

        return ret.values

    def calcIndicatorsMakePlots(self):
        if not self.count():
            return

        if self.calculatedIndicators:
            return

        class TAPlot():
            def __init__(self, **kwds):
                self.extraTA = False
                self.barGraph = False
                self.__dict__.update(kwds)

        kwds = dict(min_periods=0)

        self.taVWAP = []
        for key in ['MA1', 'MA2']:
            setattr(self, 'ta' + key, [])
            for ma in TA_LIST[key]:
                getattr(self, 'ta' + key).append(TAPlot(
                    name='MA %i' % ma.length,
                    pen=ma.pen,
                    yColumns=pd.rolling_mean(self.close, ma.length, **kwds)))

                if not self.hasVolume:
                    continue# Cant do VWAP without volume

                continue# FIXME
                self.taVWAP.append(TAPlot(
                    name='VWAP %i' % ma.length,
                    pen=(c/2 for c in ma.pen['color']),
                    yColumns=pd.rolling_mean(self.close, ma.length, **kwds)))

        ta = TA_LIST['BB']
        std = pd.rolling_std(self.close, ta.length, **kwds)
        std[0] = 0. # Dont want Nan in first entry
        self['bbMean'] = pd.rolling_mean(self.close, ta.length, **kwds)
        self['bbUpper'] = self.bbMean + ta.stdDevMult*std
        self['bbLower'] = self.bbMean - ta.stdDevMult*std
        self.taBB = TAPlot(
            name='BB %i, %g' % (ta.length, ta.stdDevMult),
            pen=ta.pen,
            yColumns=[self.bbUpper, self.bbMean, self.bbLower])

        denom = 2*ta.stdDevMult*std # == bbUpper - bbLower
        denom = np.select([denom==0., True], [1., denom])
        bbOver = (self.close - self.bbLower) / denom
        self['bbOver'] = bbOver * 2. - 1.0 # Range [-1 1]

        ta = TA_LIST['BBOver']
        self.taBBOver = TAPlot(
            name='BB Overbought/Oversold',
            pen=ta.pen,
            extraTA=True,
            yColumns=self.bbOver)

        self['typicalPrice'] = (self.high + self.low + self.close) / 3.
        self['trendTypicalPrice'] = pd.rolling_mean(self.typicalPrice, 6)
        self['upTrend'] = self.close >= self.trendTypicalPrice

        ta = TA_LIST['KC']
        midLine = pd.rolling_mean(self.typicalPrice, ta.length, **kwds)
        meanRange = pd.rolling_mean(self.high - self.low, ta.length, **kwds)

        self['kcUpper'] = midLine+ta.hlRangeMult*meanRange
        self['kcLower'] = midLine-ta.hlRangeMult*meanRange
        self.taKC = TAPlot(
            name='KC %i, %g' % (ta.length, ta.hlRangeMult),
            pen=ta.pen,
            yColumns=[self.kcUpper, self.kcLower])

        ta = TA_LIST['ADX']
        yColumns = self.calcADX(**ta.__dict__)
        self.taADX = [
            TAPlot(
                name='ADX %i' % ta.length,
                pen=ta.penADX,
                extraTA=True,
                yColumns=yColumns[:, 0]),
            TAPlot(
                name='+DI %i' % ta.length,
                pen=ta.penPlusDI,
                extraTA=True,
                yColumns=yColumns[:, 1]),
            TAPlot(
                name='-DI %i' % ta.length,
                pen=ta.penMinusDI,
                extraTA=True,
                yColumns=yColumns[:, 2]),
        ]

        ta = TA_LIST['Pulse']
        momentum, pensMomentum, bbInside, bbOutside = self.calcPulse(**ta.__dict__)
        bbCommon = dict(
            pen=None,# disable line drawing between points
            symbol='o',
            symbolSize=8,
            symbolPen=None,
            extraTA=True,
        )
        self.taPulse = [
            TAPlot(
                name='Pulse Momentum',
                extraTA=True,
                barGraph=True,
                yColumns=momentum,
                pens=pensMomentum,
                brushes=pensMomentum),
            TAPlot(
                name='BB Outside',
                x=bbOutside,
                yColumns=np.zeros((len(bbOutside),)),
                symbolBrush=(0,255,255),
                **bbCommon),
            TAPlot(
                name='BB Inside',
                x=bbInside,
                yColumns=np.zeros((len(bbInside),)),
                symbolBrush=(255,0,0),
                **bbCommon),
        ]

        self.calculatedIndicators = True

    # data must have fields: time, open, close, min, max, volume
    def createCandlestick(self, isVolume, showTrendBars):
        if not self.isOHLC: return
        if isVolume and not self.hasVolume: return

        picture = self.candleStickPictures[isVolume][showTrendBars]
        if picture:
            return picture

        picture = QtGui.QPicture()
        p = QtGui.QPainter(picture)

        w = self.timeInterval / 3.
        for e in xrange(self.count()):
            t = self.plotTimes[e]
            open, high, low, close, volume = self.getOHLCV(e)

            upBar = close >= open
            if showTrendBars:
                upTrend = self.upTrend[e]
                color = 'cyan' if upTrend else 'red'
            else:
                color = OPEN_CLOSE_COLOR[upBar]
            color = color[0]

            p.setPen(pg.mkPen(color))
            if showTrendBars and upTrend == upBar:
                p.setBrush(pg.mkBrush(color))# Solid body
            else:
                p.setBrush(pg.mkBrush('#000000'))# Make the body hollow

            if isVolume:
                y = 0.
                height = volume
            else:
                # Candlestick
                if low != high:# Weird long lines can happen on 5m Yahoo if the OHLC is all the same
                    p.drawLine(QtCore.QPointF(t, low), QtCore.QPointF(t, high))
                y = open
                height = close - open

            p.drawRect(QtCore.QRectF(t-w, y, w*2, height))

        p.end()

        self.candleStickPictures[isVolume][showTrendBars] = picture
        return picture

    def getTA(self, ta):
        ret = self.__dict__['ta' + ta]
        return wrapList(ret)

    def chartCacheKey(self, responseIdx=0):
        return ['charts', self.exchange.name, self.symbolKey, self.timeframe, str(responseIdx)]

    def downloadAndParse(self, getOrders=False):
        responses = []

        isRecent = False
        if ChartData.cacheSeconds:
            chartKey = self.chartCacheKey(0)
            cached = gCache.get(chartKey)
            isRecent = cached and (now() - cached.time).seconds < ChartData.cacheSeconds

        responseErrors = False
        def saveResponse(response):# If None, then we will try to load from the cache instead.
            chartKey = self.chartCacheKey(len(responses))

            if response:
                gCache.set(chartKey, response)
            else:
                cached = gCache.get(chartKey)
                if not cached:
                    print 'FAILED request and item not found in cache'
                    return True
                response = cached.value

            try: response = json.loads(response)
            except: pass# Yahoo uses CSV not json

            responses.append(response)
            return False

        auth = None #requests.auth.HTTPBasicAuth('rpcuser', 'rpcpass')

        dataUrls = self.exchange.dataUrls(self, getOrders)
        for urlDict in dataUrls:
            url = urlDict['url']
            post = urlDict.get('post')
            if post:
                # If there are multiple things to post, use websocket
                # websocket doesnt pickup proxy settings from environment, like requests does.
                options = {}
                HTTP_PROXY = os.environ.get('HTTP_PROXY')
                if HTTP_PROXY:
                    from urlparse import urlparse
                    p = urlparse(HTTP_PROXY)
                    options.update(http_proxy_host=p.netloc.split(':')[0], http_proxy_port=p.port)

                ws = None
                if not isRecent:
                    print url
                    try: ws = websocket.create_connection(url, **options)
                    except: pass

                for p in post:
                    p = p(responses) if hasattr(p, '__call__') else p
                    print p
                    response = None
                    if ws:
                        ws.send(json.dumps(p))
                        response = ws.recv()
                    responseErrors |= saveResponse(response)

                if ws:
                    ws.close()
            else:
                # Non-socket requests
                response = None
                if not isRecent:
                    headers = {}
                    headers.update(urlDict.get('headers', {}))
                    print url
                    try:
                        if post:
                            print post
                            response = requests.post(url, json=post, headers=headers, auth=auth)
                        else:
                            response = requests.get(url, headers=headers)
                    except:
                        pass
                    if response and response.ok:
                        response = response.content

                responseErrors |= saveResponse(response)

        if responseErrors:
            return

        self.exchange.parseData(self, responses)
