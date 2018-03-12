#!/usr/bin/env python3
# coding=utf-8

"""
Parser that uses the ENTSOE API to return the following data types.

Consumption
Production
Exchanges
Exchange Forecast
Day-ahead Price
Generation Forecast
Consumption Forecast
"""
import numpy as np
from bs4 import BeautifulSoup
from collections import defaultdict
import arrow
import os
import re
import requests

ENTSOE_ENDPOINT = 'https://transparency.entsoe.eu/api'
ENTSOE_PARAMETER_DESC = {
    'B01': 'Biomass',
    'B02': 'Fossil Brown coal/Lignite',
    'B03': 'Fossil Coal-derived gas',
    'B04': 'Fossil Gas',
    'B05': 'Fossil Hard coal',
    'B06': 'Fossil Oil',
    'B07': 'Fossil Oil shale',
    'B08': 'Fossil Peat',
    'B09': 'Geothermal',
    'B10': 'Hydro Pumped Storage',
    'B11': 'Hydro Run-of-river and poundage',
    'B12': 'Hydro Water Reservoir',
    'B13': 'Marine',
    'B14': 'Nuclear',
    'B15': 'Other renewable',
    'B16': 'Solar',
    'B17': 'Waste',
    'B18': 'Wind Offshore',
    'B19': 'Wind Onshore',
    'B20': 'Other',
}
ENTSOE_PARAMETER_BY_DESC = {v: k for k, v in ENTSOE_PARAMETER_DESC.items()}
# Define all ENTSOE zone_key <-> domain mapping
ENTSOE_DOMAIN_MAPPINGS = {
    'AL': '10YAL-KESH-----5',
    'AT': '10YAT-APG------L',
    'AX': '10Y1001A1001A46L',  # for price only; Åland has SE-SE3 area price
    'BA': '10YBA-JPCC-----D',
    'BE': '10YBE----------2',
    'BG': '10YCA-BULGARIA-R',
    'BY': '10Y1001A1001A51S',
    'CH': '10YCH-SWISSGRIDZ',
    'CZ': '10YCZ-CEPS-----N',
    'DE': '10Y1001A1001A83F',
    'DK': '10Y1001A1001A65H',
    'DK-DK1': '10YDK-1--------W',
    'DK-DK2': '10YDK-2--------M',
    'EE': '10Y1001A1001A39I',
    'ES': '10YES-REE------0',
    'FI': '10YFI-1--------U',
    'FR': '10YFR-RTE------C',
    'GB': '10YGB----------A',
    'GB-NIR': '10Y1001A1001A016',
    'GR': '10YGR-HTSO-----Y',
    'HR': '10YHR-HEP------M',
    'HU': '10YHU-MAVIR----U',
    'IE': '10YIE-1001A00010',
    'IT': '10YIT-GRTN-----B',
    'LT': '10YLT-1001A0008Q',
    'LU': '10YLU-CEGEDEL-NQ',
    'LV': '10YLV-1001A00074',
    # 'MD': 'MD',
    'ME': '10YCS-CG-TSO---S',
    'MK': '10YMK-MEPSO----8',
    'MT': '10Y1001A1001A93C',
    'NL': '10YNL----------L',
    'NO': '10YNO-0--------C',
    'NO-NO1': '10YNO-1--------2',
    'NO-NO2': '10YNO-2--------T',
    'NO-NO3': '10YNO-3--------J',
    'NO-NO4': '10YNO-4--------9',
    'NO-NO5': '10Y1001A1001A48H',
    'PL': '10YPL-AREA-----S',
    'PT': '10YPT-REN------W',
    'RO': '10YRO-TEL------P',
    'RS': '10YCS-SERBIATSOV',
    'RU': '10Y1001A1001A49F',
    'RU-KGD': '10Y1001A1001A50U',
    'SE': '10YSE-1--------K',
    'SE-SE1': '10Y1001A1001A44P',
    'SE-SE2': '10Y1001A1001A45N',
    'SE-SE3': '10Y1001A1001A46L',
    'SE-SE4': '10Y1001A1001A47J',
    'SI': '10YSI-ELES-----O',
    'SK': '10YSK-SEPS-----K',
    'TR': '10YTR-TEIAS----W',
    'UA': '10YUA-WEPS-----0'
}

# Some exchanges require specific domains
ENTSOE_EXCHANGE_DOMAIN_OVERRIDE = {
    'PL->UA': [ENTSOE_DOMAIN_MAPPINGS['PL'], '10Y1001A1001A869']
}


class QueryError(Exception):
    """Raised when a query to ENTSOE returns no matching data."""


def closest_in_time_key(x, target_datetime, datetime_key='datetime'):
    target_datetime = arrow.get(target_datetime)
    return np.abs((x[datetime_key] - target_datetime).seconds)


def check_response(response, function_name):
    """
    Searches for an error message in response if the query to ENTSOE fails.
    Returns a QueryError message containing function name and reason for failure.
    """

    soup = BeautifulSoup(response.text, 'html.parser')
    text = soup.find_all('text')
    if len(text):
        error_text = soup.find_all('text')[0].prettify()
        if 'No matching data found' in error_text:
            return
        raise QueryError('{0} failed in ENTSOE.py. Reason: {1}'.format(function_name, error_text))


def query_ENTSOE(session, params, target_datetime=None, target_datetime_range=None, span=(-48, 24)):
    """
    Makes a standard query to the ENTSOE API with a modifiable set of parameters.
    Allows an existing session to be passed.
    Raises an exception if no API token is found.
    Returns a request object.
    """
    print('query ENTSOE #####################', target_datetime, target_datetime_range, span)

    if target_datetime is None and target_datetime_range is None:
        target_datetime = arrow.utcnow()
    elif target_datetime_range:
        # if we have a range, the first datetime is the start, and we compute the span
        # as being (0, `nb of hours from start to end`)
        target_datetime = arrow.get(target_datetime_range[0])
        target_datetime_end = arrow.get(target_datetime_range[1])
        span = (0, np.math.ceil((target_datetime_end - target_datetime).total_seconds() / 3600))
        print('datetime range', target_datetime, span)
    else:
        # when querying for a specific datetime, we only look for a small span
        span = (-1, 1)
        # make sure we have an arrow object
        target_datetime = arrow.get(target_datetime)
    params['periodStart'] = target_datetime.replace(hours=span[0]).format('YYYYMMDDHH00')
    params['periodEnd'] = target_datetime.replace(hours=+span[1]).format('YYYYMMDDHH00')
    if 'ENTSOE_TOKEN' not in os.environ:
        raise Exception('No ENTSOE_TOKEN found! Please add it into secrets.env!')
    params['securityToken'] = os.environ['ENTSOE_TOKEN']
    print('ENTSOE PARAMS = {}'.format(params))
    return session.get(ENTSOE_ENDPOINT, params=params)


def query_consumption(domain, session, target_datetime=None, target_datetime_range=None):
    """Returns a string object if the query succeeds."""

    params = {
        'documentType': 'A65',
        'processType': 'A16',
        'outBiddingZone_Domain': domain,
    }
    response = query_ENTSOE(session, params, target_datetime=target_datetime,
                            target_datetime_range=target_datetime_range)
    if response.ok:
        return response.text
    else:
        check_response(response, query_consumption.__name__)


def query_production(psr_type, in_domain, session, target_datetime=None,
                     target_datetime_range=None):
    """Returns a string object if the query succeeds."""

    params = {
        'psrType': psr_type,
        'documentType': 'A75',
        'processType': 'A16',  # Realised
        'in_Domain': in_domain,
    }
    response = query_ENTSOE(session, params, target_datetime=target_datetime,
                            target_datetime_range=target_datetime_range)
    if response.ok:
        return response.text
    else:
        check_response(response, query_production.__name__)


def query_exchange(in_domain, out_domain, session, target_datetime=None,
                   target_datetime_range=None):
    """Returns a string object if the query succeeds."""

    params = {
        'documentType': 'A11',
        'in_Domain': in_domain,
        'out_Domain': out_domain,
    }
    response = query_ENTSOE(session, params, target_datetime=target_datetime,
                            target_datetime_range=target_datetime_range)
    if response.ok:
        return response.text
    else:
        check_response(response, query_exchange.__name__)


def query_exchange_forecast(in_domain, out_domain, session, target_datetime=None, target_datetime_range=None):
    """
    Gets exchange forecast for 48 hours ahead and previous 24 hours.
    Returns a string object if the query succeeds.
    """

    params = {
        'documentType': 'A09',  # Finalised schedule
        'in_Domain': in_domain,
        'out_Domain': out_domain,
    }
    response = query_ENTSOE(session, params, target_datetime=target_datetime, span=[-24, 48],
                            target_datetime_range=target_datetime_range)
    if response.ok:
        return response.text
    else:
        check_response(response, query_exchange_forecast.__name__)


def query_price(domain, session, target_datetime=None, target_datetime_range=None):
    """Returns a string object if the query succeeds."""

    params = {
        'documentType': 'A44',
        'in_Domain': domain,
        'out_Domain': domain,
    }
    response = query_ENTSOE(session, params, target_datetime=target_datetime,
                            target_datetime_range=target_datetime_range)
    if response.ok:
        return response.text
    else:
        check_response(response, query_price.__name__)


def query_generation_forecast(in_domain, session, target_datetime=None, target_datetime_range=None):
    """
    Gets generation forecast for 48 hours ahead and previous 24 hours.
    Returns a string object if the query succeeds.
    """

    # Note: this does not give a breakdown of the production
    params = {
        'documentType': 'A71',  # Generation Forecast
        'processType': 'A01',  # Realised
        'in_Domain': in_domain,
    }
    response = query_ENTSOE(session, params, target_datetime=target_datetime, span=[-24, 48],
                            target_datetime_range=target_datetime_range)
    if response.ok:
        return response.text
    else:
        check_response(response, query_generation_forecast.__name__)


def query_consumption_forecast(in_domain, session, target_datetime=None,
                               target_datetime_range=None):
    """
    Gets consumption forecast for 48 hours ahead and previous 24 hours.
    Returns a string object if the query succeeds.
    """

    params = {
        'documentType': 'A65',  # Load Forecast
        'processType': 'A01',
        'outBiddingZone_Domain': in_domain,
    }
    response = query_ENTSOE(session, params, target_datetime=target_datetime, span=[-24, 48],
                            target_datetime_range=target_datetime_range)
    if response.ok:
        return response.text
    else:
        check_response(response, query_generation_forecast.__name__)


def datetime_from_position(start, position, resolution):
    """Finds time granularity of data."""

    m = re.search('PT(\d+)([M])', resolution)
    if m:
        digits = int(m.group(1))
        scale = m.group(2)
        if scale == 'M':
            return start.replace(minutes=position * digits)
    raise NotImplementedError('Could not recognise resolution %s' % resolution)


def parse_consumption(xml_text):
    """Returns a tuple containing two lists."""

    if not xml_text:
        return None
    soup = BeautifulSoup(xml_text, 'html.parser')
    # Get all points
    quantities = []
    datetimes = []
    for timeseries in soup.find_all('timeseries'):
        resolution = timeseries.find_all('resolution')[0].contents[0]
        datetime_start = arrow.get(timeseries.find_all('start')[0].contents[0])
        for entry in timeseries.find_all('point'):
            quantities.append(float(entry.find_all('quantity')[0].contents[0]))
            position = int(entry.find_all('position')[0].contents[0])
            datetimes.append(datetime_from_position(datetime_start, position, resolution))
    return quantities, datetimes


def parse_production(xml_text):
    """Returns a tuple containing two lists."""

    if not xml_text:
        return None
    soup = BeautifulSoup(xml_text, 'html.parser')
    # Get all points
    productions = []
    datetimes = []
    for timeseries in soup.find_all('timeseries'):
        resolution = timeseries.find_all('resolution')[0].contents[0]
        datetime_start = arrow.get(timeseries.find_all('start')[0].contents[0])
        is_production = len(timeseries.find_all('inBiddingZone_Domain.mRID'.lower())) > 0
        for entry in timeseries.find_all('point'):
            quantity = float(entry.find_all('quantity')[0].contents[0])
            position = int(entry.find_all('position')[0].contents[0])
            datetime = datetime_from_position(datetime_start, position, resolution)
            try:
                i = datetimes.index(datetime)
                if is_production:
                    productions[i] += quantity
                else:
                    productions[i] -= quantity
            except ValueError:  # Not in list
                datetimes.append(datetime)
                productions.append(quantity if is_production else -1 * quantity)
    return productions, datetimes


def parse_exchange(xml_text, is_import, quantities=None, datetimes=None):
    """Returns a tuple containing two lists."""

    if not xml_text:
        return None
    quantities = quantities or []
    datetimes = datetimes or []
    soup = BeautifulSoup(xml_text, 'html.parser')
    # Get all points
    for timeseries in soup.find_all('timeseries'):
        resolution = timeseries.find_all('resolution')[0].contents[0]
        datetime_start = arrow.get(timeseries.find_all('start')[0].contents[0])
        for entry in timeseries.find_all('point'):
            quantity = float(entry.find_all('quantity')[0].contents[0])
            if not is_import:
                quantity *= -1
            position = int(entry.find_all('position')[0].contents[0])
            datetime = datetime_from_position(datetime_start, position, resolution)
            # Find out whether or not we should update the net production
            try:
                i = datetimes.index(datetime)
                quantities[i] += quantity
            except ValueError:  # Not in list
                quantities.append(quantity)
                datetimes.append(datetime)
    return quantities, datetimes


def parse_price(xml_text):
    """Returns a tuple containing three lists."""

    if not xml_text:
        return None
    soup = BeautifulSoup(xml_text, 'html.parser')
    # Get all points
    prices = []
    currencies = []
    datetimes = []
    for timeseries in soup.find_all('timeseries'):
        currency = timeseries.find_all('currency_unit.name')[0].contents[0]
        resolution = timeseries.find_all('resolution')[0].contents[0]
        datetime_start = arrow.get(timeseries.find_all('start')[0].contents[0])
        for entry in timeseries.find_all('point'):
            position = int(entry.find_all('position')[0].contents[0])
            datetime = datetime_from_position(datetime_start, position, resolution)
            prices.append(float(entry.find_all('price.amount')[0].contents[0]))
            datetimes.append(datetime)
            currencies.append(currency)
    return prices, currencies, datetimes


def parse_generation_forecast(xml_text):
    """Returns a tuple containing two lists."""

    if not xml_text:
        return None
    soup = BeautifulSoup(xml_text, 'html.parser')
    # Get all points
    values = []
    datetimes = []
    for timeseries in soup.find_all('timeseries'):
        resolution = timeseries.find_all('resolution')[0].contents[0]
        datetime_start = arrow.get(timeseries.find_all('start')[0].contents[0])
        for entry in timeseries.find_all('point'):
            position = int(entry.find_all('position')[0].contents[0])
            value = float(entry.find_all('quantity')[0].contents[0])
            datetime = datetime_from_position(datetime_start, position, resolution)
            values.append(value)
            datetimes.append(datetime)
    return values, datetimes


def parse_consumption_forecast(xml_text):
    """Returns a tuple containing two lists."""

    if not xml_text:
        return None
    soup = BeautifulSoup(xml_text, 'html.parser')
    # Get all points
    values = []
    datetimes = []
    for timeseries in soup.find_all('timeseries'):
        resolution = timeseries.find_all('resolution')[0].contents[0]
        datetime_start = arrow.get(timeseries.find_all('start')[0].contents[0])
        for entry in timeseries.find_all('point'):
            position = int(entry.find_all('position')[0].contents[0])
            value = float(entry.find_all('quantity')[0].contents[0])
            datetime = datetime_from_position(datetime_start, position, resolution)
            values.append(value)
            datetimes.append(datetime)
    return values, datetimes


def validate_production(datapoint):
    """
    Production data can sometimes be available but clearly wrong.

    The most common occurrence is when the production total is very low and
    main generation types are missing.  In reality a country's electrical grid
    could not function in this scenario.

    This function checks datapoints for a selection of countries and returns
    False if invalid and True otherwise.
    """

    codes = ('GB', 'GR', 'PT')
    if datapoint['zoneKey'] in codes:
        p = datapoint['production']
        return p.get('coal', None) is not None and p.get('gas', None) is not None
    elif datapoint['zoneKey'] == 'BE':
        p = datapoint['production']
        return p.get('nuclear', None) is not None and p.get('gas', None) is not None
    elif datapoint['zoneKey'] == 'ES':
        p = datapoint['production']
        return p.get('coal', None) is not None and p.get('nuclear', None) is not None
    elif datapoint['zoneKey'] == 'DK':
        p = datapoint['production']
        return (p.get('coal', None) is not None and p.get('gas', None) is not None
                and p.get('wind', None) is not None)
    elif datapoint['zoneKey'] == 'DE':
        p = datapoint['production']
        return (p.get('coal', None) is not None and p.get('gas', None) is not None and
                p.get('nuclear', None) is not None)
    else:
        return True


def get_biomass(values):
    if 'Biomass' in values or 'Fossil Peat' in values or 'Waste' in values:
        return values.get('Biomass', 0) + \
               values.get('Fossil Peat', 0) + \
               values.get('Waste', 0)


def get_coal(values):
    if 'Fossil Brown coal/Lignite' in values or 'Fossil Hard coal' in values:
        return values.get('Fossil Brown coal/Lignite', 0) + \
               values.get('Fossil Hard coal', 0)


def get_gas(values):
    if 'Fossil Coal-derived gas' in values or 'Fossil Gas' in values:
        return values.get('Fossil Coal-derived gas', 0) + \
               values.get('Fossil Gas', 0)


def get_hydro(values):
    if ('Hydro Run-of-river and poundage' in values or
        'Hydro Water Reservoir' in values):
        return values.get('Hydro Run-of-river and poundage', 0) + \
               values.get('Hydro Water Reservoir', 0)


def get_hydro_storage(storage_values):
    if 'Hydro Pumped Storage' in storage_values:
        return -1 * storage_values.get('Hydro Pumped Storage', 0)


def get_oil(values):
    if 'Fossil Oil' in values or 'Fossil Oil shale' in values:
        value = values.get('Fossil Oil', 0) + values.get('Fossil Oil shale', 0)
        return value if value != -1.0 else None


def get_wind(values):
    if 'Wind Onshore' in values or 'Wind Offshore' in values:
        return values.get('Wind Onshore', 0) + values.get('Wind Offshore', 0)


def get_geothermal(values):
    if 'Geothermal' in values:
        return values.get('Geothermal', 0)


def get_unknown(values):
    if ('Marine' in values or
        'Other renewable' in values or
        'Other' in values):
        return (values.get('Marine', 0) +
                values.get('Other renewable', 0) +
                values.get('Other', 0))


def fetch_consumption(zone_key, session=None, target_datetime=None, logger=None,
                      target_datetime_range=None):
    """Gets consumption for a specified zone, returns a dictionary."""
    if not session:
        session = requests.session()
    domain = ENTSOE_DOMAIN_MAPPINGS[zone_key]
    # Grab consumption
    parsed = parse_consumption(query_consumption(domain, session, target_datetime=target_datetime,
                                                 target_datetime_range=target_datetime_range))
    if parsed:
        quantities, datetimes = parsed

        # if a specific target_datetime was provided, we keep value corresponding to the
        # closest datetime
        if target_datetime:
            target_datetime = arrow.get(target_datetime)
            min_dist, dt, quantity = np.inf, 0, 0
            assert len(datetimes) and len(quantities)
            for current_dt, quant in zip(datetimes, quantities):
                dist = np.abs((current_dt - target_datetime).seconds)
                if dist < min_dist:
                    dt, min_dist, quantity = current_dt, dist, quant

        # if a time range was requested, we return everything
        elif target_datetime_range:
            return [{
                'zoneKey': zone_key,
                'datetime': dt,
                'consumption': quantity,
                'source': 'entsoe.eu'
            } for dt, quantity in zip(datetimes, quantities)]

        else:
            # else we keep the last stored value
            dt, quantity = datetimes[-1].datetime, quantities[-1]
        data = {
            'zoneKey': zone_key,
            'datetime': dt,
            'consumption': quantity,
            'source': 'entsoe.eu'
        }

        return data


def fetch_production(zone_key, session=None, target_datetime=None, logger=None,
                     target_datetime_range=None):
    """
    Gets values and corresponding datetimes for all production types in the
    specified zone. Removes any values that are in the future or don't have
    a datetime associated with them.
    Returns a list of dictionaries that have been validated.
    """
    if not session:
        session = requests.session()
    domain = ENTSOE_DOMAIN_MAPPINGS[zone_key]
    # Create a double hashmap with keys (datetime, parameter)
    production_hashmap = defaultdict(lambda: {})
    # Grab production
    for k in ENTSOE_PARAMETER_DESC.keys():
        parsed = parse_production(query_production(k, domain, session,
                                                   target_datetime=target_datetime,
                                                   target_datetime_range=target_datetime_range))
        if parsed:
            productions, datetimes = parsed
            for i in range(len(datetimes)):
                production_hashmap[datetimes[i]][k] = productions[i]

    # Remove all dates in the future
    production_dates = sorted(set(production_hashmap.keys()), reverse=True)
    production_dates = list(filter(lambda x: x <= arrow.now(), production_dates))
    if not len(production_dates):
        return None
    # Only take fully observed elements
    max_counts = max(map(lambda d: len(production_hashmap[d].keys()),
                         production_dates))
    production_dates = filter(lambda d: len(production_hashmap[d].keys()) == max_counts,
                              production_dates)

    data = []
    for production_date in production_dates:
        production_values = {ENTSOE_PARAMETER_DESC[k]: v for k, v in
                             production_hashmap[production_date].items()}

        data.append({
            'zoneKey': zone_key,
            'datetime': production_date.datetime,
            'production': {
                'biomass': get_biomass(production_values),
                'coal': get_coal(production_values),
                'gas': get_gas(production_values),
                'hydro': get_hydro(production_values),
                'nuclear': production_values.get('Nuclear', None),
                'oil': get_oil(production_values),
                'solar': production_values.get('Solar', None),
                'wind': get_wind(production_values),
                'geothermal': get_geothermal(production_values),
                'unknown': get_unknown(production_values)
            },
            'storage': {
                'hydro': get_hydro_storage(production_values),
            },
            'source': 'entsoe.eu'
        })

    to_return = list(filter(validate_production, data))

    if not target_datetime:
        return to_return

    # if target_datetime was provided, only keep the most relevant
    target_datetime = arrow.get(target_datetime)
    if not len(to_return):
        return None

    most_relevant = sorted(to_return, key=lambda x: closest_in_time_key(x, target_datetime))[0]
    return [most_relevant]


def fetch_exchange(zone_key1, zone_key2, session=None, target_datetime=None, logger=None,
                   target_datetime_range=None):
    """
    Gets exchange status between two specified zones.
    Removes any datapoints that are in the future.
    Returns a list of dictionaries.
    """
    if not session:
        session = requests.session()
    sorted_zone_keys = sorted([zone_key1, zone_key2])
    key = '->'.join(sorted_zone_keys)
    if key in ENTSOE_EXCHANGE_DOMAIN_OVERRIDE:
        domain1, domain2 = ENTSOE_EXCHANGE_DOMAIN_OVERRIDE[key]
    else:
        domain1 = ENTSOE_DOMAIN_MAPPINGS[zone_key1]
        domain2 = ENTSOE_DOMAIN_MAPPINGS[zone_key2]
    # Create a hashmap with key (datetime)
    exchange_hashmap = {}
    # Grab exchange
    # Import
    parsed = parse_exchange(
        query_exchange(domain1, domain2, session, target_datetime=target_datetime,
                       target_datetime_range=target_datetime_range),
        is_import=True)
    if parsed:
        # Export
        parsed = parse_exchange(
            xml_text=query_exchange(domain2, domain1, session, target_datetime=target_datetime,
                                    target_datetime_range=target_datetime_range),
            is_import=False, quantities=parsed[0], datetimes=parsed[1])
        if parsed:
            quantities, datetimes = parsed
            for i in range(len(quantities)):
                exchange_hashmap[datetimes[i]] = quantities[i]

    # Remove all dates in the future
    exchange_dates = sorted(set(exchange_hashmap.keys()), reverse=True)
    exchange_dates = list(filter(lambda x: x <= arrow.now(), exchange_dates))
    if not len(exchange_dates):
        return None
    data = []
    for exchange_date in exchange_dates:
        net_flow = exchange_hashmap[exchange_date]
        data.append({
            'sortedZoneKeys': key,
            'datetime': exchange_date.datetime,
            'netFlow': net_flow if zone_key1[0] == sorted_zone_keys else -1 * net_flow,
            'source': 'entsoe.eu'
        })

    if target_datetime:
        # only keep most relevant
        most_relevant = sorted(data, key=lambda x: closest_in_time_key(x, target_datetime))[0]
        return [most_relevant]

    return data


def fetch_exchange_forecast(zone_key1, zone_key2, session=None, target_datetime=None,
                            logger=None, target_datetime_range=None):
    """
    Gets exchange forecast between two specified zones.
    Returns a list of dictionaries.
    """
    if target_datetime:
        raise NotImplementedError('This parser is not yet able to parse past dates')

    if not session:
        session = requests.session()
    domain1 = ENTSOE_DOMAIN_MAPPINGS[zone_key1]
    domain2 = ENTSOE_DOMAIN_MAPPINGS[zone_key2]
    # Create a hashmap with key (datetime)
    exchange_hashmap = {}
    # Grab exchange
    # Import
    parsed = parse_exchange(
        query_exchange_forecast(domain1, domain2, session, target_datetime=target_datetime,
                                target_datetime_range=target_datetime_range),
        is_import=True)
    if parsed:
        # Export
        parsed = parse_exchange(
            xml_text=query_exchange_forecast(domain2, domain1, session,
                                             target_datetime=target_datetime,
                                             target_datetime_range=target_datetime_range),
            is_import=False, quantities=parsed[0], datetimes=parsed[1])
        if parsed:
            quantities, datetimes = parsed
            for i in range(len(quantities)):
                exchange_hashmap[datetimes[i]] = quantities[i]

    # Remove all dates in the future
    sorted_zone_keys = sorted([zone_key1, zone_key2])
    exchange_dates = list(sorted(set(exchange_hashmap.keys()), reverse=True))
    if not len(exchange_dates):
        return None
    data = []
    for exchange_date in exchange_dates:
        netFlow = exchange_hashmap[exchange_date]
        data.append({
            'sortedZoneKeys': '->'.join(sorted_zone_keys),
            'datetime': exchange_date.datetime,
            'netFlow': netFlow if zone_key1[0] == sorted_zone_keys else -1 * netFlow,
            'source': 'entsoe.eu'
        })
    return data


def fetch_price(zone_key, session=None, target_datetime=None, logger=None,
                target_datetime_range=None):
    """
    Gets day-ahead price for specified zone.
    Returns a list of dictionaries.
    """
    # Note: This is day-ahead prices
    if not session:
        session = requests.session()
    domain = ENTSOE_DOMAIN_MAPPINGS[zone_key]
    # Grab consumption
    parsed = parse_price(query_price(domain, session, target_datetime=target_datetime,
                                     target_datetime_range=target_datetime_range))
    if parsed:
        data = []
        prices, currencies, datetimes = parsed
        for i in range(len(prices)):
            data.append({
                'zoneKey': zone_key,
                'datetime': datetimes[i].datetime,
                'currency': currencies[i],
                'price': prices[i],
                'source': 'entsoe.eu'
            })

        if target_datetime:
            # only keep most relevant
            most_relevant = sorted(data, key=lambda x: closest_in_time_key(x, target_datetime))[0]
            return [most_relevant]

        return data


def fetch_generation_forecast(zone_key, session=None, target_datetime=None,
                              target_datetime_range=None, logger=None):
    """
    Gets generation forecast for specified zone.
    Returns a list of dictionaries.
    """
    if target_datetime:
        raise NotImplementedError('This parser is not yet able to parse past dates')

    if not session:
        session = requests.session()
    domain = ENTSOE_DOMAIN_MAPPINGS[zone_key]
    # Grab consumption
    parsed = parse_generation_forecast(query_generation_forecast(
        domain, session, target_datetime=target_datetime,
        target_datetime_range=target_datetime_range))
    if parsed:
        data = []
        values, datetimes = parsed
        for i in range(len(values)):
            data.append({
                'zoneKey': zone_key,
                'datetime': datetimes[i].datetime,
                'value': values[i],
                'source': 'entsoe.eu'
            })

        return data


def fetch_consumption_forecast(zone_key, session=None, target_datetime=None,
                               target_datetime_range=None, logger=None):
    """
    Gets consumption forecast for specified zone.
    Returns a list of dictionaries.
    """
    if target_datetime:
        raise NotImplementedError('This parser is not yet able to parse past dates')

    if not session:
        session = requests.session()
    domain = ENTSOE_DOMAIN_MAPPINGS[zone_key]
    # Grab consumption
    parsed = parse_consumption_forecast(query_consumption_forecast(
        domain, session, target_datetime=target_datetime,
        target_datetime_range=target_datetime_range))
    if parsed:
        data = []
        values, datetimes = parsed
        for i in range(len(values)):
            data.append({
                'zoneKey': zone_key,
                'datetime': datetimes[i].datetime,
                'value': values[i],
                'source': 'entsoe.eu'
            })

        return data
