import asyncio
import aiohttp
import os
import time
import hmac
import hashlib
import json
import urllib.parse
import math
from decimal import Decimal
from typing import Dict, List, Optional, Any
from ASTER_codes.api_client import ApiClient
from strategy_logic import DeltaNeutralLogic

# Base URLs for the APIs
FUTURES_BASE_URL = "https://fapi.asterdex.com"
SPOT_BASE_URL = "https://sapi.asterdex.com"


class AsterApiManager:
    """
    Unified API manager for both Aster Perpetual and Spot markets.
    Handles all API communications with proper authentication and precision formatting.
    """

    def __init__(self, api_user: str, api_signer: str, api_private_key: str,
                 apiv1_public: str, apiv1_private: str):
        """
        Initialize the API manager with all required credentials.
        """
        self.api_user = api_user
        self.api_signer = api_signer
        self.api_private_key = api_private_key
        self.apiv1_public = apiv1_public
        self.apiv1_private = apiv1_private

        self.perp_client = ApiClient(api_user, api_signer, api_private_key)
        self.session = None
        self.spot_exchange_info = None
        self.perp_exchange_info = None

    # --- Exchange Info and Formatting Helpers ---

    async def _get_spot_exchange_info(self, force_refresh: bool = False) -> dict:
        """Fetches and caches spot exchange information."""
        if not self.spot_exchange_info or force_refresh:
            self.spot_exchange_info = await self._make_spot_request('GET', '/api/v1/exchangeInfo')
        return self.spot_exchange_info

    async def _get_perp_exchange_info(self, force_refresh: bool = False) -> dict:
        """Fetches and caches perpetual exchange information."""
        if not self.perp_exchange_info or force_refresh:
            if not self.perp_client.session:
                self.perp_client.session = aiohttp.ClientSession()
            self.perp_exchange_info = await self.perp_client.get_exchange_info()
        return self.perp_exchange_info

    def _truncate(self, value: float, precision: int) -> float:
        """Truncates a float to a given precision without rounding."""
        if precision < 0: precision = 0
        if precision == 0:
            return math.floor(value)
        factor = 10.0 ** precision
        return math.floor(value * factor) / factor

    async def _get_formatted_order_params(self, symbol: str, market_type: str, price: Optional[float] = None, quantity: Optional[float] = None, quote_quantity: Optional[float] = None) -> dict:
        """Fetches symbol filters and formats order parameters to the correct precision."""
        if market_type == 'spot':
            exchange_info = await self._get_spot_exchange_info()
        elif market_type == 'perp':
            exchange_info = await self._get_perp_exchange_info()
        else:
            return {}

        symbol_info = next((s for s in exchange_info.get('symbols', []) if s['symbol'] == symbol), None)
        if not symbol_info:
            raise ValueError(f"Symbol {symbol} not found in {market_type} exchange info.")

        params = {}

        # Format price based on PRICE_FILTER (tickSize)
        if price is not None:
            price_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
            if price_filter:
                tick_size_str = price_filter['tickSize']
                precision = abs(Decimal(tick_size_str).as_tuple().exponent)
                price = self._truncate(price, precision)
                params['price'] = f"{price:.{precision}f}"
            else:
                params['price'] = str(price)

        # Format quantity based on LOT_SIZE (stepSize)
        if quantity is not None:
            lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
            if lot_size_filter:
                step_size_str = lot_size_filter['stepSize']
                precision = abs(Decimal(step_size_str).as_tuple().exponent)
                quantity = self._truncate(quantity, precision)
                params['quantity'] = f"{quantity:.{precision}f}"
            else:
                params['quantity'] = str(quantity)

        # Format quote quantity for spot market buys based on quoteAssetPrecision
        if quote_quantity is not None and market_type == 'spot':
            precision = symbol_info.get('quoteAssetPrecision', 2) # Default to 2 for safety if not found
            quote_quantity = self._truncate(quote_quantity, precision)
            params['quoteOrderQty'] = f"{quote_quantity:.{precision}f}"

        return params

    # --- Core Request Methods ---

    def _create_spot_signature(self, params: dict) -> str:
        """Create HMAC-SHA256 signature for spot API requests."""
        query_string = urllib.parse.urlencode(params)
        return hmac.new(self.apiv1_private.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

    async def _make_spot_request(self, method: str, path: str, params: dict = None, signed: bool = False, suppress_errors: bool = False) -> dict:
        """Generic method for making requests to the Spot API."""
        if params is None:
            params = {}
        if not self.session:
            self.session = aiohttp.ClientSession()

        url = f"{SPOT_BASE_URL}{path}"
        headers = {'X-MBX-APIKEY': self.apiv1_public}

        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['recvWindow'] = 5000
            params['signature'] = self._create_spot_signature(params)

        async with self.session.request(method, url, params=params, headers=headers) as response:
            if not response.ok:
                error_body = await response.text()
                if not suppress_errors:
                    print(f"API Error: {response.status}, Body: {error_body}")
            response.raise_for_status()
            return await response.json()

    # --- Public Data Fetching Methods ---

    async def get_perp_account_info(self) -> dict:
        """Get perpetuals account information."""
        if not self.perp_client.session:
            self.perp_client.session = aiohttp.ClientSession()
        return await self.perp_client.signed_request('GET', '/fapi/v3/account')

    async def get_spot_account_balances(self) -> list:
        """Get spot account balances."""
        response = await self._make_spot_request('GET', '/api/v1/account', signed=True)
        return response.get('balances', [])

    async def get_funding_rate_history(self, symbol: str, limit: int = 50) -> list:
        """Get funding rate history for a symbol."""
        if not self.perp_client.session:
            self.perp_client.session = aiohttp.ClientSession()
        url = f"{FUTURES_BASE_URL}/fapi/v1/fundingRate"
        params = {'symbol': symbol, 'limit': limit}
        async with self.perp_client.session.get(url, params=params) as response:
            response.raise_for_status()
            return await response.json()

    async def get_perp_book_ticker(self, symbol: str) -> dict:
        """Get perpetuals book ticker for a symbol."""
        if not self.perp_client.session:
            self.perp_client.session = aiohttp.ClientSession()
        url = f"{FUTURES_BASE_URL}/fapi/v1/ticker/bookTicker"
        params = {'symbol': symbol}
        async with self.perp_client.session.get(url, params=params) as response:
            response.raise_for_status()
            return await response.json()

    async def get_spot_book_ticker(self, symbol: str, suppress_errors: bool = False) -> dict:
        """Get spot book ticker for a symbol."""
        return await self._make_spot_request('GET', '/api/v1/ticker/bookTicker', params={'symbol': symbol}, suppress_errors=suppress_errors)

    # --- Public Execution Methods (Write Actions) ---

    async def place_perp_order(self, symbol: str, price: str, quantity: str, side: str, reduce_only: bool = False) -> dict:
        """Place a perpetuals limit order with correct precision."""
        if not self.perp_client.session:
            self.perp_client.session = aiohttp.ClientSession()
        
        formatted_params = await self._get_formatted_order_params(
            symbol=symbol, market_type='perp', price=float(price), quantity=float(quantity)
        )
        return await self.perp_client.place_order(symbol, formatted_params['price'], formatted_params['quantity'], side, reduce_only)

    async def place_perp_market_order(self, symbol: str, quantity: str, side: str) -> dict:
        """Place a perpetuals market order with correct precision."""
        if not self.perp_client.session:
            self.perp_client.session = aiohttp.ClientSession()
        
        formatted_params = await self._get_formatted_order_params(
            symbol=symbol, market_type='perp', quantity=float(quantity)
        )
        
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': formatted_params['quantity']
        }
        return await self.perp_client.signed_request('POST', '/fapi/v3/order', params)

    async def place_spot_buy_market_order(self, symbol: str, quote_quantity: str) -> dict:
        """Place a spot market buy order with correct precision."""
        formatted_params = await self._get_formatted_order_params(
            symbol=symbol, market_type='spot', quote_quantity=float(quote_quantity)
        )
        params = {'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quoteOrderQty': formatted_params['quoteOrderQty']}
        return await self._make_spot_request('POST', '/api/v1/order', params=params, signed=True)

    async def place_spot_sell_market_order(self, symbol: str, base_quantity: str) -> dict:
        """Place a spot market sell order with correct precision."""
        formatted_params = await self._get_formatted_order_params(
            symbol=symbol, market_type='spot', quantity=float(base_quantity)
        )
        params = {'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': formatted_params['quantity']}
        return await self._make_spot_request('POST', '/api/v1/order', params=params, signed=True)

    async def close_perp_position(self, symbol: str, quantity: str, side_to_close: str) -> dict:
        """Close a perpetuals position using a market order with correct precision."""
        if not self.perp_client.session:
            self.perp_client.session = aiohttp.ClientSession()

        formatted_params = await self._get_formatted_order_params(
            symbol=symbol, market_type='perp', quantity=float(quantity)
        )
        params = {
            'symbol': symbol, 'side': side_to_close, 'type': 'MARKET',
            'quantity': formatted_params['quantity'], 'reduceOnly': 'true', 'positionSide': 'BOTH'
        }
        return await self.perp_client.signed_request('POST', '/fapi/v3/order', params)

    # --- Transfer Methods ---

    async def transfer_between_spot_and_perp(self, asset: str, amount: float, direction: str) -> dict:
        """
        Transfer assets between spot and perpetual accounts.

        Args:
            asset: Asset to transfer (e.g., 'USDT')
            amount: Amount to transfer
            direction: 'SPOT_TO_PERP' or 'PERP_TO_SPOT'

        Returns:
            Transfer response with transaction ID and status
        """
        if not self.perp_client.session:
            self.perp_client.session = aiohttp.ClientSession()

        # Generate unique transaction ID
        client_tran_id = f"transfer_{int(time.time() * 1000000)}"

        # Map direction to API parameter
        direction_map = {
            'SPOT_TO_PERP': 'SPOT_FUTURE',
            'PERP_TO_SPOT': 'FUTURE_SPOT'
        }

        if direction not in direction_map:
            raise ValueError(f"Invalid direction: {direction}. Must be 'SPOT_TO_PERP' or 'PERP_TO_SPOT'")

        params = {
            'asset': asset,
            'amount': str(amount),
            'clientTranId': client_tran_id,
            'kindType': direction_map[direction]
        }

        return await self.perp_client.signed_request('POST', '/fapi/v3/asset/wallet/transfer', params)

    async def rebalance_usdt_50_50(self) -> dict:
        """
        Automatically rebalance USDT to be 50/50 between spot and perpetual accounts.

        Returns:
            Dictionary with rebalance details and transfer result (if transfer was needed)
        """
        # Get current balances
        spot_balances = await self.get_spot_account_balances()
        perp_account = await self.get_perp_account_info()

        # Extract USDT balances
        spot_usdt = next((float(b.get('free', 0)) for b in spot_balances if b.get('asset') == 'USDT'), 0.0)

        # Get USDT from perpetual account assets
        perp_assets = perp_account.get('assets', [])
        perp_usdt = next((float(a.get('walletBalance', 0)) for a in perp_assets if a.get('asset') == 'USDT'), 0.0)

        total_usdt = spot_usdt + perp_usdt
        target_each = total_usdt / 2

        # Calculate transfer needed
        spot_difference = target_each - spot_usdt

        result = {
            'current_spot_usdt': spot_usdt,
            'current_perp_usdt': perp_usdt,
            'total_usdt': total_usdt,
            'target_each': target_each,
            'transfer_needed': abs(spot_difference) > 1.0,  # Only transfer if difference > $1
            'transfer_amount': abs(spot_difference),
            'transfer_direction': None,
            'transfer_result': None
        }

        # Perform transfer if needed (minimum $1 difference to avoid micro-transfers)
        if abs(spot_difference) > 1.0:
            if spot_difference > 0:
                # Need to transfer from perp to spot
                result['transfer_direction'] = 'PERP_TO_SPOT'
                result['transfer_result'] = await self.transfer_between_spot_and_perp(
                    'USDT', abs(spot_difference), 'PERP_TO_SPOT'
                )
            else:
                # Need to transfer from spot to perp
                result['transfer_direction'] = 'SPOT_TO_PERP'
                result['transfer_result'] = await self.transfer_between_spot_and_perp(
                    'USDT', abs(spot_difference), 'SPOT_TO_PERP'
                )

        return result

    # --- Symbol Discovery and Analysis ---

    async def get_available_spot_symbols(self) -> List[str]:
        """Get list of all available spot trading symbols."""
        try:
            exchange_info = await self._get_spot_exchange_info()
            if exchange_info and 'symbols' in exchange_info:
                return sorted([s['symbol'] for s in exchange_info['symbols'] if s.get('status') == 'TRADING'])
            return []
        except Exception as e:
            print(f"Error fetching spot symbols: {e}")
            return []

    async def get_available_perp_symbols(self) -> List[str]:
        """Get list of all available perpetual trading symbols."""
        try:
            exchange_info = await self._get_perp_exchange_info()
            if exchange_info and 'symbols' in exchange_info:
                return sorted([s['symbol'] for s in exchange_info['symbols'] if s.get('status') == 'TRADING'])
            return []
        except Exception as e:
            print(f"Error fetching perpetual symbols: {e}")
            return []

    async def get_perp_symbol_filter(self, symbol: str, filter_type: str) -> Optional[Dict]:
        """Retrieves a specific filter for a perpetual symbol from exchange info."""
        try:
            exchange_info = await self._get_perp_exchange_info()
            symbol_info = next((s for s in exchange_info.get('symbols', []) if s['symbol'] == symbol), None)
            if symbol_info:
                return next((f for f in symbol_info['filters'] if f['filterType'] == filter_type), None)
        except Exception as e:
            print(f"Error getting perp filter for {symbol}: {e}")
        return None

    async def discover_delta_neutral_pairs(self) -> List[str]:
        """Dynamically discover which pairs are available for delta-neutral strategies."""
        try:
            spot_symbols, perp_symbols = await asyncio.gather(
                self.get_available_spot_symbols(),
                self.get_available_perp_symbols(),
                return_exceptions=True
            )
            if isinstance(spot_symbols, Exception) or isinstance(perp_symbols, Exception):
                spot_symbols, perp_symbols = [], []

            from strategy_logic import DeltaNeutralLogic
            return DeltaNeutralLogic.find_delta_neutral_pairs(spot_symbols, perp_symbols)
        except Exception as e:
            print(f"Error discovering delta-neutral pairs: {e}")
            return []

    async def analyze_current_positions(self) -> Dict[str, Dict[str, Any]]:
        """Analyze current open positions across spot and perpetual markets."""
        try:
            # Fetch all required data concurrently
            perp_info, spot_info, perp_account, spot_balances = await asyncio.gather(
                self._get_perp_exchange_info(),
                self._get_spot_exchange_info(),
                self.get_perp_account_info(),
                self.get_spot_account_balances(),
                return_exceptions=True
            )
            if isinstance(perp_info, Exception) or isinstance(spot_info, Exception) or isinstance(perp_account, Exception) or isinstance(spot_balances, Exception):
                return {}

            # Prepare data for strategy logic
            spot_lookup = {b.get('asset', ''): float(b.get('free', '0')) + float(b.get('locked', '0')) for b in spot_balances}
            perp_symbol_map = {s['symbol']: s for s in perp_info.get('symbols', [])}
            perp_positions = perp_account.get('positions', [])

            # Filter for positions with non-zero amounts and fetch current prices
            active_positions = [p for p in perp_positions if float(p.get('positionAmt', 0)) != 0]
            if active_positions:
                # Fetch current mark prices for all active positions
                price_tasks = [self.get_perp_book_ticker(p['symbol']) for p in active_positions]
                price_results = await asyncio.gather(*price_tasks, return_exceptions=True)

                # Update positions with current mark prices
                for i, pos in enumerate(active_positions):
                    price_data = price_results[i]
                    if not isinstance(price_data, Exception) and price_data.get('bidPrice') and price_data.get('askPrice'):
                        # Use mid-price as mark price
                        bid_price = float(price_data['bidPrice'])
                        ask_price = float(price_data['askPrice'])
                        pos['markPrice'] = (bid_price + ask_price) / 2
                    # If price fetch fails, keep existing markPrice or set to 0

            # Use strategy logic for computational analysis
            analysis = DeltaNeutralLogic.analyze_position_data(
                perp_positions=perp_positions,
                spot_balances=spot_lookup,
                perp_symbol_map=perp_symbol_map
            )

            return analysis
        except Exception as e:
            print(f"Error analyzing positions: {e}")
            return {}

    async def close(self):
        """Close the HTTP session and perpetual client session."""
        if self.session and not self.session.closed:
            await self.session.close()
        if self.perp_client.session and not self.perp_client.session.closed:
            await self.perp_client.session.close()
