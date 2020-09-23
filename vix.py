import yfinance as yf
import datetime as dt

class VIXCalc():

    def __init__(self):
        self.tickers = []
        self.label = None

    def set_label(self, label_string):
        self.label = label_string

    def set_tickers(self, ticker_list):
        self.tickers = ticker_list

    def append_ticker(self, ticker_string):
        self.tickers.append(ticker_string)

    def calculate_composite_VIX(self):
        measurements = []
        for ticker in self.tickers:
            VIX_calc = self.calculate_VIX(ticker)
            measurements.append(VIX_calc)

        composite_VIX = sum(measurements)/len(measurements)
        return composite_VIX
    
    def calculate_VIX(self, ticker):
        ticker_api = yf.Ticker(ticker)
        ticker_info = ticker_api.info
        price = ticker_info['regularMarketPrice']
        options_expirations = ticker_api.options
        now = dt.datetime.now()
        
        thirty_forward_date = now + dt.timedelta(30)

        near_term_date = []
        for d in options_expirations:
            if d <= thirty_forward_date:
                near_term_date.append(d)
        near_term_date = near_term_date[-1]

        next_term_date = []
        for d in options_expirations:
            if d > thirty_forward_date:
                next_term_date.append(d)
        next_term_date = next_term_date[-1]

        T_near = 
        T_next = 

        near_term_options = ticker_api.option_chain(str(near_term_date))
        next_term_options = ticker_api.option_chain(str(next_term_date))


