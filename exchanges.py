import numpy as np, datetime as dt
import sys, inspect
import requests, json, urllib
from utils import *
import ig

def parseOrderbook(self, data, response):

    for bidAsk in range(2):

        all_entries = response[['bids', 'asks'][bidAsk]]

        prices = []
        amounts = [0]
        accumAmount = 0
        for entry in all_entries:

            price = float(entry[0])
            amount = entry[1]

            accumAmount += amount
            prices.append(price)
            amounts.append(accumAmount)

        data.orderwall.append([prices, amounts])

EXCHANGES = []
class Exchange(object):
    def setDefault(self, **kwds):
        for name, default in kwds.iteritems():
            setattr(self, name, getattr(self, name, default))
    def __init__(self):
        self.nameWithShortcut = self.name
        self.name = self.name.replace('&', '')

        c = gCache.get(['symbols', self.name])
        symbols = c.value if c else {}

        self.setDefault(
            filterGaps=False,
            # Yahoo & Google daily chart doesnt update until the end of the day,
            # so fill with minute data in the meantime.
            appendMinuteData=False,
            symbols=symbols,
            defaultTimeframe='d')

    def findSymbol(self, partialName):
        partialName = partialName.upper()
        # Search for a symbol by its partial name
        return next((k for k in sorted(self.symbols) if partialName.upper() in k.upper()), partialName)

    def onChartLoad(self, cg): pass
    def onSearchOpen(self): pass
    def onSearchString(self, searchString): pass

    def saveSymbols(self):
        gCache.set(['symbols', self.name], self.symbols)

class ExchangeWithSearch(Exchange):
    def __init__(self):
        super(ExchangeWithSearch, self).__init__()

YEARS_DAILY = 3
YEARS_WEEKLY = 10

class _1(Exchange):
    name = 'Bitcoinwisdom'
    def __init__(self):
        Exchange.__init__(self)

    def dataUrls(self, data, getOrders):
        symbol=self.symbols[data.symbolKey]
        return [{'url': 'https://s2.bitcoinwisdom.com/period?step=%i&sid=866d9ac4&symbol=%s' % (intervalSeconds(data.intervalKey), symbol)}] +\
            ([{'url': 'https://s2.bitcoinwisdom.com/depth?symbol=%s&sid=866d9ac4' % symbol}] if getOrders else [])

    def parseData(self, data, responses):
        response = responses[0]
        tohlcv = []
        for entry in response:
            time = entry[0]
            open = entry[3]
            high = entry[5]
            low = entry[6]
            close = entry[4]
            volume = entry[7]

            tohlcv.append([time, open, high, low, close, volume])
        data.setOHLC(tohlcv)

        if len(responses) > 1:
            parseOrderbook(self, data, responses[1]['return'])

    symbols = {
        'Bitstamp USD': 'bitstampbtcusd',
        'Bitfinex USD': 'bitfinexbtcusd',
        'OKCoin CNY': 'okcoinbtccny'
    }

    intervals = [
        '1d',
        '12h',
        '6h',
        '1h',
        '1m',
    ]

class _2(Exchange):
    name = 'Bitcoincharts'
    def __init__(self):
        Exchange.__init__(self)

    def dataUrls(self, data, getOrders):
        symbol=self.symbols[data.symbolKey]
        return [{'url': 'http://bitcoincharts.com/charts/chart.json?m=%s&r=%s&i=%s'
        % (symbol, self.intervals[data.intervalKey][1], self.intervals[data.intervalKey][0])}]

    def parseData(self, data, responses):
        response = responses[0]
        tohlcv = []
        for entry in response:
            # Click on "Load raw data" to see some rows containing this
            if any([f == 1.7e+308 for f in entry]):
                continue
            tohlcv.append(entry)
        data.setOHLC(tohlcv)

    symbols = {
        'Bitfinex': 'bitfinexUSD',
        'BitStamp': 'bitstampUSD',
        'Coinfloor': 'coinfloorGBP',
        'Coinbase': 'coinbaseUSD',
    }

    intervals = {
        '1d': ('Daily', ''),
        '1h': ('Hourly', '150'),
        '5m': ('5-min', '10'),
        '1m': ('1-min', '2'),
    }

def getPoloniexMarkets():
    response = requests.get('https://poloniex.com/public?command=return24hVolume')
    j = json.loads(response.content)
    marketVolume = {}
    for key, value in j.iteritems():
        sp = key.split('_')
        if len(sp) != 2 or sp[0] != 'BTC':
            continue
        marketVolume[sp[1] + '/BTC'] = float(value['BTC'])

    return [m for m,v in sorted(marketVolume.items(), key=lambda pair: -pair[1])]

def savePoloniexMarkets():
    symbols = getPoloniexMarkets()[:50]
    print symbols
    exchange = findExchange('Poloniex')
    exchange.symbols = symbols
    exchange.saveSymbols()

class _3(Exchange):
    name = 'Poloniex'
    def __init__(self):
        Exchange.__init__(self)

    # https://poloniex.com/support/api
    # Valid intervals are 300, 900, 1800, 7200, 14400, and 86400

    def dataUrls(self, data, getOrders):
        symbol = (lambda sp: sp[1] + '_' + sp[0])(data.symbolKey.split('/'))
        return [{'url': 'https://poloniex.com/public?command=returnChartData&currencyPair=' + symbol +\
        '&start=' + str(timestamp(now() - dt.timedelta(days=self.intervals[data.intervalKey]))) + '&end=9999999999&' +\
        'period=%i' % intervalSeconds(data.intervalKey)
        }] +\
        ([{'url': 'https://poloniex.com/public?command=returnOrderBook&currencyPair=%s&depth=1000'
        % (symbol)}] if getOrders else [])

    def parseData(self, data, responses):
        response = responses[0]
        tohlcv = []
        for entry in response[1:]:# First Polo entry has open of 50
            time = entry['date']
            open = entry['open']
            high = entry['high']
            low = entry['low']
            close = entry['close']
            #volume = entry['quoteVolume']
            # Use base so markets can be compared in terms of BTC volume
            volume = entry['volume']

            tohlcv.append([time, open, high, low, close, volume])
        data.setOHLC(tohlcv)

        if len(responses) > 1:
            parseOrderbook(self, data, responses[1])

    intervals = {
        '1d': 1000,
        '4h': 100,
        '2h': 100,
        '30m': 100,
        '15m': 100,
        '5m': 100,
    }

class _4(Exchange):
    name = 'Blockchain.info'
    def __init__(self):
        Exchange.__init__(self)

    def dataUrls(self, data, getOrders):
        return [
            {'url': 'https://blockchain.info/charts/n-transactions?showDataPoints=false&timespan=all&show_header=true&daysAverageString=1&scale=0&format=json&address='},
        ]

    def parseData(self, data, responses):
        response = responses[0]
        ty = []
        for entry in response['values']:
            ty.append([entry['x'], entry['y']])
        data.setOHLC(ty)

    symbols = {'Daily Transactions': None}
    intervals = {}

def addStockLinks(cg, stockSymbol, nasdaqOrNyse):
    def addLink(url, desc):
        cg.links[desc] = url + stockSymbol
    exch = {'LON': 'LSE', 'NYSE': 'NYSE', 'NASDAQ': 'NasdaqGS'}.get(nasdaqOrNyse)
    if exch: addLink('https://simplywall.st/' + exch + ':', 'Simplywall.st')
    addLink('http://www.barchart.com/quotes/stocks/', 'Barchart')
    addLink('http://finance.yahoo.com/q?s=', 'Yahoo News')
    if exch: addLink('https://www.tradingview.com/chart/?symbol=' + exch + ':', 'TradingView')
    addLink('http://stocktwits.com/symbol/', 'StockTwits')
    addLink('https://twitter.com/search?q=', 'Twitter')

class _6(ExchangeWithSearch):
    name = '&Yahoo Finance'
    filterGaps = True
    appendMinuteData = True
    def __init__(self):
        ExchangeWithSearch.__init__(self)

    def onSearchString(self, searchString):

        url = 'https://beta.finance.yahoo.com/_finance_doubledown/api/resource/searchassist;gossipConfig={"isJSONP":true,"queryKey":"query","resultAccessor":"ResultSet.Result","suggestionTitleAccessor":"symbol","suggestionMeta":["symbol"],"url":{"protocol":"https","host":"s.yimg.com","path":"/xb/v6/finance/autocomplete","query":{"appid":"yahoo.com","nresults":10,"output":"yjsonp","region":"US","lang":"en-US"}}};searchTerm=' \
            + searchString + '?bkt=DD_Test_4&device=desktop&intl=us&lang=en-US&partner=none&region=US&site=finance&tz=America/Los_Angeles&ver=0.4.528'
        response = requests.get(url)

        j = json.loads(response.content)
        j = j.get('items', [])

        ret = [match['name'] + ' | ' + match['symbol'] for match in j]

        return ret

    def dataUrls(self, data, getOrders):
        # Url used by Yahoo's website charts.
        return [{'url': 'https://finance-yql.media.yahoo.com/v7/finance/chart/' + data.symbolKey + \
        '?period2=' + str(timestamp(now())) + \
        '&period1=' + str(timestamp(now() - dt.timedelta(days=self.intervals[data.intervalKey]))) + \
        '&interval=' + data.intervalKey.upper() + ('k' if data.intervalKey == '1w' else '') + \
        '&indicators=quote&includeTimestamps=true&includePrePost=true&events=div|split|earn&corsDomain=finance.yahoo.com'}]

    def parseData(self, data, responses):
        result = responses[0]['chart']['result']
        if not result:
            return

        result = result[0]
        timestamp = result.get('timestamp', [])# No list if there's no data eg on weekend
        quote = result['indicators']['quote'][0]

        tohlcv = []
        for i in xrange(len(timestamp)):
            time = timestamp[i]
            open = quote['open'][i]
            high = quote['high'][i]
            low = quote['low'][i]
            close = quote['close'][i]
            volume = quote['volume'][i]

            entry = [open, high, low, close, volume]

            for i in range(len(entry)):
                try: f = float(entry[i])
                except: f = 0. # Volume is sometimes None
                entry[i] = f

            if all([not f for f in entry]):# Skip all-zero entries
                continue

            tohlcv.append([time] + entry)

        data.setOHLC(tohlcv)

    intervals = {
        '1w': YEARS_WEEKLY*365,
        '1d': YEARS_DAILY*365,
        '1h': 89,
        '5m': 10,
        '1m': 1,
    }

class _7(ExchangeWithSearch):
    name = '&Google Finance'
    filterGaps = True
    appendMinuteData = True
    defaultTimeframe = 'h'# Daily doesnt work for some low market cap stocks
    def __init__(self):
        ExchangeWithSearch.__init__(self)

    def onSearchString(self, searchString):
        url = 'https://www.google.com/finance/match?matchtype=matchall&ei=I8lhVunvB5GQUPehn-gP&q=' + searchString
        response = requests.get(url)

        j = json.loads(response.content)
        j = j.get('matches', [])

        ret = []
        for match in j:
            symbolType = match['e']
            symbolKey = match['t']
            if symbolType == 'CURRENCY':
                symbolKey += 'USD'# Search results for currencies dont include the quote currency
            ret.append(match['n'] + ' | ' + symbolType + ':' + symbolKey)
        return ret

    def onChartLoad(exchange, cg):
        sp = cg.data.symbolKey.split(':')
        addStockLinks(cg, sp[1], sp[0])

    def dataUrls(self, data, getOrders):
        # Url used by Google's flash charts.
        return [{'url': 'https://www.google.co.uk/finance/getprices?q=' + data.symbolKey.split(':')[1] + \
        '&x=' + data.symbolKey.split(':')[0] + \
        '&i=' + str(intervalSeconds(data.intervalKey)) + \
        '&p=' + self.intervals[data.intervalKey] + '&f=d,c,v,o,h,l&df=cpct&auto=1' + \
        '&ts=' + str(timestamp(now()))}]

    def parseData(self, data, responses):
        lines = responses[0].splitlines()[7:]
        seconds = intervalSeconds(data.intervalKey)

        tohlcv = []
        for line in lines:
            if 'TIMEZONE_OFFSET' in line:
                continue

            entry = line.split(',')
            if entry[0][0] == 'a':
                time = timeBase = int(entry[0][1:])
            else:
                time = timeBase + int(entry[0]) * seconds

            open = entry[4]
            high = entry[2]
            low = entry[3]
            close = entry[1]
            volume = entry[5]

            entry = [open, high, low, close, volume]
            entry = [float(s) for s in entry]
            tohlcv.append([time] + entry)

        data.setOHLC(tohlcv)

    intervals = {
        # For 1w|1d, '40Y' is allowed but not needed.
        '1w': '%iY' % YEARS_WEEKLY,
        '1d': '%iY' % YEARS_DAILY,
        '1h': '100d',
        '5m': '100d',
        '2m': '100d',
        '1m': '100d',
    }

IGINDEX_DATETIME_FORMAT = '%Y/%m/%d/%H/%M/%S'
class _8(ExchangeWithSearch):
    name = '&IG'
    filterGaps = True
    def __init__(self):
        ExchangeWithSearch.__init__(self)

    def onSearchString(self, searchString):
        url = 'markets?searchTerm=' + urllib.quote(searchString, safe='')
        content = ig.callAPI(url)

        ret = []
        if not content:
            return ret
        for market in content.get('markets', []):
    #        if market['expiry'] != 'DFB':
    #            continue# Just get the non-expiring one
            ret.append(market['instrumentName'] + '(' + market['expiry'] + ') | ' + market['epic'])

        return ret

    onSearchOpen = lambda self: ig.getHeaders()# Display the login dialog if neccessary
    def onChartLoad(exchange, cg):
        cg.position = exchange.positions.get(cg.data.symbolKey)

    def dataUrls(self, data, getOrders):
        return [
            {'url': 'https://api.ig.com/chart/snapshot/' + data.symbolKey + '/' + self.intervals[data.intervalKey][0] +\
            '/batch/start/' + (now() - dt.timedelta(days=self.intervals[data.intervalKey][1])).strftime(IGINDEX_DATETIME_FORMAT) +\
            '/0/end/' + now().strftime(IGINDEX_DATETIME_FORMAT) + '/999' +\
            '?format=json&siteId=igi&locale=en_GB',
            'headers': ig.getHeaders()}
        ]

    def parseData(self, data, responses):
        def calcMid(priceDict):
            ask = priceDict.get('ask')
            bid = priceDict.get('bid')
            ret, denom = 0., 0
            if ask:
                ret += ask
                denom += 1
            if bid:
                ret += bid
                denom += 1
            return ret / (denom or 1)

        response = responses[0]
        tohlcv = []
        for interval in response['intervalsDataPoints']:
            for dataPoint in interval['dataPoints']:
                close = dataPoint['closePrice']
                if not close:
                    continue# Last entry has neither ask nor bid
                close = calcMid(close)
                time = dataPoint['timestamp'] / 1000
                open = calcMid(dataPoint['openPrice'])
                high = calcMid(dataPoint['highPrice'])
                low = calcMid(dataPoint['lowPrice'])
                volume = 0.

                entry = [open, high, low, close, volume]
                tohlcv.append([time] + entry)

        data.setOHLC(tohlcv)

    # http://labs.ig.com/faq
    intervals = {
        '1d': ('1/DAY', 365 * 15),
        '1h': ('1/HOUR', 360),
        '10m': ('10/MINUTE', 40),
        '5m': ('5/MINUTE', 40),
        '1m': ('1/MINUTE', 40),
    }

mod = sys.modules[__name__]
# Automatically instantiate all the classes in this module
for name, cls in inspect.getmembers(mod):
    if not inspect.isclass(cls):
        continue
    if hasattr(cls, 'dataUrls'):
        EXCHANGES.append(cls())

def findExchange(exchangeName):
    market = next((market for market in EXCHANGES if exchangeName.lower() in market.name.lower()), None)
    return market

def addYahooGoogleSymbols():
    google = findExchange('Google')
    yahoo = findExchange('Yahoo')

    forex = [
        'AUDUSD',
        'EURCHF',
        'EURGBP',
        'EURJPY',
        'EURUSD',
        'GBPEUR',
        'GBPJPY',
        'GBPUSD',
        'USDCAD',
        'USDCHF',
        'USDJPY',
    ]
    for f in forex:
        # Yahoo is better for currencies
        yahoo.symbols[f + '=X'] = None
        #google.symbols['CURRENCY:' + f] = None

    FUTURES_MONTHS = [
        'F',
        'G',
        'H',
        'J',
        'K',
        'M',
        'N',
        'Q',
        'U',
        'V',
        'X',
        'Z',
    ]
    futureTemplates = [
        # Second column is number of months ahead
        ('Copper|HG%s%i.CMX', 2),
        ('Crude Oil|CL%s%i.NYM', 2),
        ('Gold|GC%s%i.CMX', 2),
        ('Silver|SI%s%i.CMX', 3),
    ]
    for f in futureTemplates:
        expiryDate = dt.datetime.now() + dt.timedelta(days=f[1]*30)
        s = f[0] % (FUTURES_MONTHS[expiryDate.month-1], expiryDate.year % 100)
        yahoo.symbols[s] = None

    other = [
        'S&P 500|^GSPC',
        'S&P/ASX 200|^AXJO',
        'Dow Jones|^DJI',
        'FTSE 100|^FTSE',
        'Nikkei 225|^N225',
        # Daily data is thin
        #'Euro Stocks 50|^STOXX50E',
        #'CSI 300|000300.SS',
    ]
    for s in other:
        yahoo.symbols[s] = None

    other = [
        'Euro Stocks 50|INDEXSTOXX:SX5E',
        'CSI 300 Index|SHA:000300',
        'VIX S&P 500|INDEXCBOE:VIX',
        'Dollar Index|INDEXDJX:USDOLLAR',
    ]
    for s in other:
        google.symbols[s] = None
addYahooGoogleSymbols()
