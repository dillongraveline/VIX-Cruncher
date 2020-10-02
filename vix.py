import yfinance as yf
import datetime as dt
import requests
import json
import pandas as pd
from fredapi import Fred
from scipy import interpolate
import math
import warnings
warnings.filterwarnings('ignore')
import stockquotes

class VIXCalc():

    def __init__(self):
        self.tickers = []
        self.label = None
        self._eodoptions_api_key = None
        self._fred_api_key = c47800ecdce41a71d698aef2d4ebc599

    def set_eodoptions_api_key(self, apikey):
        self._eodoptions_api_key = apikey

    def set_label(self, label_string):
        self.label = label_string

    def set_tickers(self, ticker_list):
        self.tickers = ticker_list

    def append_ticker(self, ticker_string, weight):
        self.tickers.append([ticker_string, weight])

    def calculate_composite_VIX(self):
        measurements = []
        for ticker in self.tickers:
            try:
                VIX_calc = self.calculate_VIX(ticker[0])
                measurements.append([VIX_calc, ticker[1]])
            except:
                print(f"ERROR: There was a problem calcualting {ticker[0]}'s VIX. \n This could be due to a lack of options volume.")
        
        
        total_weight = 0
        for measurement in measurements:
            total_weight += measurement[1]

        composite_VIX = 0

        # Calculate the weighted average of all individual VIX calculations for each stock.
        for measurement in measurements:
            composite_VIX += measurement[0] * (measurement[1] / total_weight)
        
        print(f"The composite VIX for sector {self.label} is: {composite_VIX}.")

        return composite_VIX
    
    def calculate_VIX(self, ticker):
        print(f"Calculating VIX for: {ticker}...")

        options_data = self.options_data_parser(ticker)
        
        midnight = dt.datetime.combine(dt.date.today(), dt.datetime.min.time())
        midnight = midnight + dt.timedelta(1)

        M_current_day = midnight - options_data['now']
        M_current_day = M_current_day.total_seconds() / 60
        
        # Assuming options expire at 5:30PM on the expiriation date
        M_settlement_day = 1050

        M_other_days_near = dt.datetime.combine(options_data['near_term_date'], dt.datetime.min.time()) - midnight
        M_other_days_near = M_other_days_near.total_seconds() / 60

        M_other_days_next = dt.datetime.combine(options_data['next_term_date'], dt.datetime.min.time()) - midnight
        M_other_days_next = M_other_days_next.total_seconds() / 60

        T_near = (M_current_day + M_settlement_day + M_other_days_near) / 525600
        T_next = (M_current_day + M_settlement_day + M_other_days_next) / 525600

        # Generate constant maturity treasury rates for each expiration using cubic spline interpolation.
        R1 = self.risk_free_calc(options_data['near_term_date'])
        R2 = self.risk_free_calc(options_data['next_term_date'])

        # Determine the forward stock level, F, by identifying the strike price at which the absolute difference between the call and put prices is smallest.
        F_near = self.forward_level(options_data['near_term_options'], R1, T_near)
        F_next = self.forward_level(options_data['next_term_options'], R2, T_next)
        
        # Obtain the K0 strike price
        K0_near = self.K0_calc(options_data['near_term_options'], F_near)
        K0_next = self.K0_calc(options_data['next_term_options'], F_next)

        # Filter the option chain using the K0 price.
        K_options_chain_calls_near = self.K_options_chain_calls_filter(options_data['near_term_options'], K0_near)
        K_options_chain_puts_near = self.K_options_chain_puts_filter(options_data['near_term_options'], K0_near)
        K_options_chain_calls_next = self.K_options_chain_calls_filter(options_data['next_term_options'], K0_next)
        K_options_chain_puts_next = self.K_options_chain_puts_filter(options_data['next_term_options'], K0_next)

        # Combine the call and put option chains to form a composite chain.
        K_near_chain = self.K_chain_combiner(K_options_chain_calls_near, K_options_chain_puts_near, K0_near)
        K_next_chain = self.K_chain_combiner(K_options_chain_calls_next, K_options_chain_puts_next, K0_next)
        

        # Calculate vol for near term options
        V_near = self.calc_vol(K_near_chain, T_near, R1, K0_near, F_near)        

        # Calculate vol for next term options
        V_next = self.calc_vol(K_next_chain, T_next, R2, K0_next, F_next)

        # Define N values for VIX Calc
        NT1 = M_current_day + M_settlement_day + M_other_days_near
        NT2 = M_current_day + M_settlement_day + M_other_days_next
        N30 = 43200
        N365 = 525600

        # VIX is the interpolation between near and next term options to form the vol of 30 day maturity options.
        VIX =  100 * math.sqrt(
            ((T_near * V_near * ((NT2 - N30) / (NT2 - NT1)))
            + (T_next * V_next * ((N30 - NT1)/(NT2 - NT1))))
            * (N365 / N30)
            )
        
        print(f"The VIX for {ticker} is: {VIX}")

        return VIX

    
    def calc_vol(self, option_chain, T, R, K0, F):
        contributions = []
        for idx in option_chain.index:
            if idx == 0:
                delta_K = option_chain.loc[idx+1, 'strike'] - option_chain.loc[idx, 'strike']
            elif idx == option_chain.index[-1]:
                delta_K = option_chain.loc[idx, 'strike'] - option_chain.loc[idx-1, 'strike']
            else:
                delta_K = (option_chain.loc[idx+1, 'strike'] - option_chain.loc[idx-1, 'strike'])/2
            K = option_chain.loc[idx, 'strike']
            K_squared = K**2
            Q = option_chain.loc[idx, 'midpoint']
            composite = (delta_K / K_squared) * (math.e ** (R * T)) * Q
            contributions.append(composite) 
        
        contributions = [x for x in contributions if str(x) != 'nan']
        total_contributions = sum(contributions)
        
        left_term = (2 / T) * total_contributions
        
        right_term = (1/T) * ((F/K0) - 1)**2
        
        vol = left_term - right_term

        return vol


    def K_chain_combiner(self, calls, puts, K0):
        # Generate call dataframe
        calls['midpoint'] = (calls['bid'] + calls['ask']) / 2
        call_df = calls[['strike', 'midpoint']]
        call_df['type'] = "Call"

        # Generate put dataframe
        puts['midpoint'] = (puts['bid'] + puts['ask']) / 2
        put_df = puts[['strike', 'midpoint']]
        put_df['type'] = "Put"

        pass_append = False
        try:
            # Find index value of K0 strike
            call_k = call_df.index[call_df['strike'] == K0].tolist()
            call_k = call_k[0]

            # Find index value of K0 strike
            put_k = put_df.index[put_df['strike'] == K0].tolist()

            put_k = put_k[0]

            # Find avg midpoint price at K0 strike
            avg = (call_df.loc[call_k, 'midpoint'] + put_df.loc[put_k, 'midpoint']) / 2

            # Drop K0 from both dataframes
            call_df.drop(call_k, inplace=True)
            put_df.drop(put_k, inplace=True)
        
        except:
            pass_append = True

        # Merge call and put frames
        frames = [put_df, call_df]
        result = pd.concat(frames)

        if pass_append == False:
            # Append call/put midpoint average row
            result = result.append({'strike': K0, 'type': "Put/Call Average", 'midpoint': avg}, ignore_index=True)
        
        else:
            pass

        # Sort by strike price and reset_index of dataframe
        result.sort_values(by='strike', inplace=True)
        result.reset_index(inplace=True)

        return result

    def K_options_chain_calls_filter(self, options_chain, K0):
        calls = options_chain.calls
        calls = calls[calls['strike'] >= K0]
        calls['include'] = True
        calls = calls.reset_index(drop=True)
        trigger = False

        for index, row in calls.iterrows():
            if trigger == False:
                if row['bid'] == 0 and calls.loc[index-1, 'include'] == True:
                    calls.ix[index, 'include'] = False
                elif row['bid'] == 0 and calls.loc[index-1, 'include'] == False:
                    calls.ix[index, 'include'] = False
                    trigger = True
            elif trigger == True:
                calls.ix[index, 'include'] = False
        
        chain_df = calls[calls['include'] == True]
        return chain_df
            


    def K_options_chain_puts_filter(self, options_chain, K0):
        puts = options_chain.puts
        puts = puts[puts['strike'] <= K0]
        
        puts['include'] = True
        puts = puts.reset_index(drop=True)
        trigger = False

        for idx in reversed(puts.index):
            if trigger == False:
                if puts.loc[idx, 'bid'] == 0 and puts.loc[idx+1, 'include'] == True:
                    puts.ix[idx, 'include'] = False
                elif puts.loc[idx, 'bid'] == 0 and puts.loc[idx+1, 'include'] == False:
                    puts.ix[idx, 'include'] = False
                    trigger = True
            elif trigger == True:
                puts.ix[idx, 'include'] = False
        
        chain_df = puts[puts['include'] == True]
        return chain_df

    def K0_calc(self, options_chain, F):
        calls = options_chain.calls
        puts = options_chain.puts
        
        merged_df = calls.merge(puts, on='strike', how='left')
        
        less_than = merged_df[merged_df['strike'] < F]
        less_than.sort_values(by='strike', inplace=True)
        
        K0 = less_than['strike'].iloc[-1]
        
        return K0


    def risk_free_calc(self, date):
        try:
            fred = Fred(api_key=self._fred_api_key)
        except:
            print("YOU NEED TO SET THE FRED API KEY USING THE SET_FRED_API_KEY METHOD")

        # Obtain the yield curve CMT data from FRED
        one_month = fred.get_series_latest_release('DGS1MO').iloc[-1]
        three_month = fred.get_series_latest_release('DGS3MO').iloc[-1]
        six_month = fred.get_series_latest_release('DGS6MO').iloc[-1]
        one_year = fred.get_series_latest_release('DGS1').iloc[-1]
        two_year = fred.get_series_latest_release('DGS2').iloc[-1]
        three_year = fred.get_series_latest_release('DGS3').iloc[-1]
        five_year = fred.get_series_latest_release('DGS5').iloc[-1]

        days_array = [30.4167, 91.2501, 182.5, 365, 730, 1095, 1825]
        yield_array = [one_month, three_month, six_month, one_year, two_year, three_year, five_year]

        # Cubic spline interpolation to derive the CMT rates for the near and next term maturities.
        tck = interpolate.splrep(days_array, yield_array, s=0)

        # Calculate days from date
        today = dt.datetime.now()
        day_delta = date - today
        days = day_delta.days

        # Back out interpolated yield
        y_new = interpolate.splev(days, tck)

        return y_new

    def forward_level(self, option_chain, R, T):
        calls = option_chain.calls
        puts = option_chain.puts
       

        calls['midpointCalls'] = (calls['bid'] + puts['ask']) / 2
        puts['midpointPuts'] = (puts['bid'] + puts['ask']) / 2

        puts = puts[['strike', 'midpointPuts']]
        calls = calls[['strike', 'midpointCalls']]

        calls.dropna(inplace=True)
        puts.dropna(inplace=True)

        merged_df = calls.merge(puts, on='strike', how='outer')
        
        merged_df['difference'] = merged_df['midpointCalls'] - merged_df['midpointPuts']
        min_index = merged_df['difference'].idxmin()
        
        f_strike = merged_df.loc[min_index, 'strike']
        call_price = merged_df.loc[min_index, 'midpointCalls']
        put_price = merged_df.loc[min_index, 'midpointPuts']
        
        # Calculate the F Index Value
        F = f_strike + math.e**(R*T) * (call_price - put_price)

        return F
        
    def options_data_parser(self, ticker):
        '''
        options_data is a dictionary with all the necessary data
        '''
        now = dt.datetime.now()
        thirty_forward_date = now + dt.timedelta(30)

        # Obtaining data from the yahoo finance API
        ticker_api = yf.Ticker(ticker)
        
        try:
            ticker_info = ticker_api.info
            try:
                price = ticker_info['regularMarketPrice']
            except KeyError:
                price = ticker_info
        except:
            ticker_api_backup = stockquotes.Stock(ticker)
            price = ticker_api_backup.current_price

        # Obtaining the options expiration dates from the yahoo finance API
        options_expirations = ticker_api.options

        # Searching for optimal near term date
        near_term_date = []
        for d in options_expirations:
            d = dt.datetime.strptime(d, '%Y-%m-%d')
            if d <= thirty_forward_date:
                near_term_date.append(d)
        near_term_date = near_term_date[-1]

        # Searching for optimal next term date
        next_term_date = []
        for d in options_expirations:
            d = dt.datetime.strptime(d, '%Y-%m-%d')
            if d > thirty_forward_date:
                next_term_date.append(d)
        next_term_date = next_term_date[0]

        # Using the yfinance api
        near_term_options = ticker_api.option_chain(str(near_term_date.date()))
        next_term_options = ticker_api.option_chain(str(next_term_date.date()))

        # Generating contract name based on options naming conventions (UNFINISHED)
        '''
        near_term_options_contract = f"{ticker}" + 
        next_term_options_contract
        '''

        # Using eodoptionsdata api (UNFINISHED)
        '''
        near_term_options_request_url = https://eodhistoricaldata.com/api/options/AAPL.US?api_token={your_api_key}
        next_term_options_request_url = https://eodhistoricaldata.com/api/options/AAPL.US?api_token={your_api_key}
        '''

        options_data = {
        'now': now, 
        'thirty_forward_date': thirty_forward_date,
        'near_term_date': near_term_date,
        'next_term_date': next_term_date,
        'price':price,
        'options_expirations': options_expirations,
        'near_term_options': near_term_options,
        'next_term_options': next_term_options,
        }

        return options_data


# This will be replaced with a graphical user interface, but for now, it is done by code.
InfoTech = VIXCalc()
InfoTech.set_label("Information Technology")
Healthcare = VIXCalc()
Healthcare.set_label("Healthcare")
Financials = VIXCalc()
Financials.set_label("Financials")
Cons = VIXCalc()
Cons.set_label("Consumers")
Comm = VIXCalc()
Comm.set_label("Communication Services")
Utilities = VIXCalc()
Utilities.set_label("Utilities")
Industrials = VIXCalc()
Industrials.set_label("Industrials")
Energy = VIXCalc()
Energy.set_label("Energy")
RealEstate = VIXCalc()
RealEstate.set_label("Real Estate")
Materials = VIXCalc()
Materials.set_label("Materials")

# Add companies to each Sector Object

# Info Tech
InfoTech.append_ticker("ADBE.O", 27)
InfoTech.append_ticker("XQQ.TO", 36.3)
InfoTech.append_ticker("PROSY.PK", 20.4)
InfoTech.append_ticker("TDOC.K", 16.2)

# Healthcare
Healthcare.append_ticker("ALC", 22.5)
Healthcare.append_ticker("MDT", 25.4)
Healthcare.append_ticker("SRPT.O", 28.9)
Healthcare.append_ticker("SIS.TO", 23.2)

# Financials
Financials.append_ticker("AXP", 20.2)
Financials.append_ticker("BAC", 18)
Financials.append_ticker("BMO.TO", 21)
Financials.append_ticker("FLI.TO", 10.7)
Financials.append_ticker("EWBC.O", 16.9)
Financials.append_ticker("SIVB.O", 13.2)

# Consumers (combining discretionary and staples, therefore sum(weights) > 100. The algo auto scales the weights to add to 100.)
Cons.append_ticker("ATZ.TO", 43.8)
Cons.append_ticker("XCD.TO", 51.5)
Cons.append_ticker("L.TO", 100)
Cons.append_ticker("TSX:PSG", 4.8)

# Communication Services
Comm.append_ticker("COMM.TO", 37.4)
Comm.append_ticker("FB", 62.6)

# Utilities
Utilities.append_ticker("EMA.TO", 64.3)
Utilities.append_ticker("SSEZY.PK", 35.7)

# Industrials
Industrials.append_ticker("FCT.MI", 28.7)
Industrials.append_ticker("XGI.TO", 23.2)
Industrials.append_ticker("TDG", 48.1)

# Energy
Energy.append_ticker("MPC", 22.2)
Energy.append_ticker("PXT.TO", 14.3)
Energy.append_ticker("PPL.TO", 28.5)
Energy.append_ticker("VII.TO", 21.4)
Energy.append_ticker("SU.TO", 13.6)

# Real Estate
RealEstate.append_ticker("MGP", 39.9)
RealEstate.append_ticker("PLD", 60.1)

# Materials
Materials.append_ticker("NILSY.PK", 36.2)
Materials.append_ticker("SUM", 29)
Materials.append_ticker("XMA.TO", 34.8)

# Calculate Composite VIX Values
InfoTech.calculate_composite_VIX()
Healthcare.calculate_composite_VIX()
Financials.calculate_composite_VIX()
Cons.calculate_composite_VIX()
Comm.calculate_composite_VIX()
Utilities.calculate_composite_VIX()
Industrials.calculate_composite_VIX()
Energy.calculate_composite_VIX()
RealEstate.calculate_composite_VIX()
Materials.calculate_composite_VIX()