from pyqtgraph import QtGui
import requests, json, time, os
from utils import *
import exchanges, watchlists

IG_URL = 'https://api.ig.com/gateway/deal/'

class Login(QtGui.QDialog):
    def __init__(self, apiKey, parent=None):
        super(Login, self).__init__(parent)
        self.apiKey = apiKey
        self.setWindowTitle('IG Login')
        self.textName = QtGui.QLineEdit(self)
        self.textPass = QtGui.QLineEdit(self)
        self.textPass.setEchoMode(QtGui.QLineEdit.Password)
        self.saveLogin = QtGui.QCheckBox('Save Login', self)
        self.saveLogin.setChecked(True)
        self.buttonLogin = QtGui.QPushButton('Login', self)
        self.buttonLogin.clicked.connect(self.handleLogin)
        layout = QtGui.QVBoxLayout(self)
        layout.addWidget(self.textName)
        layout.addWidget(self.textPass)
        layout.addWidget(self.saveLogin)
        layout.addWidget(self.buttonLogin)

    def handleLogin(self):
        url = IG_URL + '/session'
        headers = { 'X-IG-API-KEY': self.apiKey }
        body = {
            'identifier': str(self.textName.text()),
            'password': str(self.textPass.text())
        }
        response = requests.post(url, json=body, headers=headers)

        if response.ok:
            self.headers = response.headers
            self.accept()
        else:
            QtGui.QMessageBox.warning(
                self, 'Error', 'Failed to login: ' + str(response))

def getHeaders(getNewTokens=False):
    NEEDED_API_KEY = 'X-IG-API-KEY'
    NEEDED_TOKENS = ['X-SECURITY-TOKEN', 'CST']# Also need the api

    headersFile = CACHE_DIR + '/ig_headers.json'

    ig = exchanges.findExchange('IG')

    ig.headers = getattr(ig, 'headers', {})
    if not ig.headers:
        ig.headers = json.loads(open(headersFile).read())
    apiKey = ig.headers.get(NEEDED_API_KEY)

    if not apiKey:
        print('ERROR: Could not find ' + NEEDED_API_KEY)
        raise BaseException

    try:
        if getNewTokens or any(not ig.headers.get(k) for k in NEEDED_TOKENS):
            # We havent saved some of the tokens, so login to IG and get them
            login = Login(apiKey)
            if login.exec_() != QtGui.QDialog.Accepted:
                os._exit(1) # directly exit without throwing an exception

            for k in NEEDED_TOKENS:
                ig.headers[k] = login.headers[k]

            print('New login header: ' + str(ig.headers))

            if login.saveLogin.isChecked():
                json.dump(ig.headers, open(headersFile, 'w'))

        if not hasattr(ig, 'positions'):
            getOpenPositions()# Will raise if offline
    except:
        pass# Allow viewing charts offline if possible

    return ig.headers

def callAPI(url):
    getNewTokens = False
    while True:
        headers = getHeaders(getNewTokens)
        response = requests.get(IG_URL + url, headers=headers)
        try:
            content = json.loads(response.content)
        except:
            return None #unknown error

        if 'client-token-invalid' in content.get('errorCode', ''):
            # Delete the tokens and try again
            getNewTokens = True
            continue

        break

    return content

def getWorkingOrders():
    watchlist = []
    content = callAPI('workingorders')
    print content
    for order in content['workingOrders']:
        marketData = order['marketData']
        watchlist.append(str(marketData['instrumentName'] + '|' + str(marketData['epic']) + ' / IG'))
    return watchlist

def getOpenPositions():
    exchange = exchanges.findExchange('IG')# Also save it in the exchange object

    watchlist = []
    exchange.positions = {}
    content = callAPI('positions')
    for order in content['positions']:
        market = order['market']
        position = order['position']
        print 'position: ', position

        exchange.positions[market['epic']] = dict(
            instrumentName=market['instrumentName'],
            createdDate=timestamp(dt.datetime.strptime(position['createdDate'], '%Y/%m/%d %H:%M:%S:%f')),
            openLevel=position['openLevel'],
            stopLevel=position['stopLevel'],
            limitLevel=position['limitLevel'],
        )

        watchlist.append(str(market['instrumentName'] + '|' + str(market['epic']) + ' / IG'))
    print exchange.positions
    return watchlist

WATCHLIST_OPEN_ORDERS = 'IG Open Orders'
IG_INDEX_WATCHLIST = 'IG Index, '
IG_INDEX_MAP = {
    'LON':      IG_INDEX_WATCHLIST + 'Shares - UK',
    'NASDAQ':   IG_INDEX_WATCHLIST + 'Shares - US',
    'NYSE':     IG_INDEX_WATCHLIST + 'Shares - US',
}

def setWatchlist(name, watchlist):
    watchlist = [str(s) for s in watchlist]
    gCache.set(watchlists.cacheKey(name), watchlist)
    print 'Watchlist: %s, #symbols: %i' % (name, len(watchlist))

def importIGIndexCSVWatchlist(filename):
    reutersToGoogle = {
        #'AX': 'ASX',
        #'VI': 'VIE',
        #'TO': 'TSE',
        #'HK': 'HKG',
        'L':  'LON',
        'O':  'NASDAQ',
        'N':  'NYSE'
    }

    allWatchlists = {w: [] for w in set(IG_INDEX_MAP.values())}

    import pandas as pd
    #df = pd.read_csv(filename, header=1)
    df = pd.read_excel(filename, header=1)
    for i, row in df.iterrows():
        if i < 2: continue

        if row['New Positions'] != 'Yes':
            continue

        symbolDotMarket = row['Reuters'].split('.')
        if len(symbolDotMarket) != 2:
            continue
        symbol = symbolDotMarket[0]
        market = symbolDotMarket[1]
        market = reutersToGoogle.get(market)
        if market == None:
            continue

        marketStr = row['Description'] + '|' + market + ':' + symbol + '/Google'
        print marketStr
        watchlist = allWatchlists[IG_INDEX_MAP[market]]
        watchlist.append(marketStr)

    for name, watchlist in allWatchlists.items():
        setWatchlist(name, watchlist)

def importIGIndexEpicsWatchlist():

    #TOP_LEVEL_NODES = ['Shares - UK']
    TOP_LEVEL_NODES = ['Shares - US']

    for topLevelNode in TOP_LEVEL_NODES:
        watchlistName = IG_INDEX_WATCHLIST + topLevelNode
        watchlist = gCache.get(watchlists.cacheKey(watchlistName))
        watchlist = watchlist.value if watchlist else []
        def getNode(id):
            url = 'marketnavigation'
            if id:
                url += '/' + id
            content = callAPI(url)
            time.sleep(2) # Per-app non-trading requests per minute: 60 - https://labs.ig.com/faq

            for node in content['nodes'] or []:
                name = node['name']
                if not id and (name not in TOP_LEVEL_NODES):
                    continue

                if any([len(name) >= 5 and name in s for s in watchlist]):
                    print 'Already have ', name, ', skipping'
                    continue

                subNode = getNode(node['id'])

            # Now that we have the market node, save the DFB version
            for market in content.get('markets') or []:
                if market['expiry'] != 'DFB':
                    continue# Just get the non-expiring one

                marketStr = str(market['instrumentName']) + '|' + str(market['epic']) + '/IG'
                print 'Added: ', marketStr

                watchlist.append(marketStr)
                setWatchlist(watchlistName, watchlist)

        getNode(None)
