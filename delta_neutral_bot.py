#!/usr/bin/env python3
"""
Terminal-based UI and orchestrator for the delta-neutral funding rate farming bot.
"""

import asyncio
import os
import sys
import argparse
from datetime import datetime
from collections import deque
from dotenv import load_dotenv
from colorama import init, Fore, Style

# Platform-specific imports for non-blocking input
try:
    import msvcrt
except ImportError:
    import termios
    import tty

from aster_api_manager import AsterApiManager
from strategy_logic import DeltaNeutralLogic

# Load environment variables from .env file
load_dotenv()

# Initialize colorama for cross-platform colored text
init()

class DashboardApp:
    """
    The main application class that orchestrates the API manager, strategy logic,
    and terminal-based user interface.
    """

    def __init__(self, is_test_run=False):
        """Initialize the application, API manager, and initial state."""
        self.api_manager = AsterApiManager(
            api_user=os.getenv('API_USER'),
            api_signer=os.getenv('API_SIGNER'),
            api_private_key=os.getenv('API_PRIVATE_KEY'),
            apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
            apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
        )
        self.logic = DeltaNeutralLogic()
        self.running = True
        self.refresh_interval = 30  # seconds
        self.is_test_run = is_test_run

        # State variables to hold dashboard data
        self.last_updated = "Never"
        self.portfolio_value = 0.0
        self.positions = []
        self.spot_balances = []
        self.opportunities = []
        self.log_messages = deque(maxlen=3)  # Store last 3 log messages
        self.perp_margin_balance = 0.0
        self.perp_usdt_balance = 0.0
        self.perp_usdc_balance = 0.0
        self.perp_usdf_balance = 0.0
        self.spot_usdt_balance = 0.0
        self.raw_perp_positions = []
        self.funding_rate_cache = None  # To hold on-demand funding rate scan results

    async def run(self):
        """Main entry point to start the application."""
        self._add_log("Application started. Initializing and performing first data fetch...")
        
        try:
            # Perform an initial fetch to populate caches before the user can interact
            await self._fetch_and_update_data()
            self._render_dashboard()
        except Exception as e:
            self._add_log(f"{Fore.RED}Initial data fetch failed: {e}{Style.RESET_ALL}")
            self.running = False # Stop if the initial fetch fails

        # Now start the main loop and input handler if the initial fetch was successful
        if self.running:
            main_loop_task = asyncio.create_task(self._main_loop())
            
            if not self.is_test_run:
                input_handler_task = asyncio.create_task(self._handle_user_input())
                tasks = [main_loop_task, input_handler_task]
            else:
                # For test run, we don't need the main loop, just the initial fetch
                tasks = [main_loop_task]

            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                self._add_log("Shutdown signal received.")
            finally:
                self._add_log("Closing API connections...")
                await self.api_manager.close()
                if not self.is_test_run:
                    self._add_log("Shutdown complete.")
                    print(Fore.YELLOW + "Application has been shut down." + Style.RESET_ALL)

    async def _main_loop(self):
        """The core loop that periodically fetches data and refreshes the dashboard."""
        # The first fetch is already done by run(), so we start with a sleep.
        while self.running:
            await asyncio.sleep(self.refresh_interval)
            
            try:
                await self._fetch_and_update_data()
                self._render_dashboard()

                if self.is_test_run:
                    self._add_log("Test run complete. Exiting.")
                    print("\n" + Fore.GREEN + "Test run successful. Dashboard rendered once." + Style.RESET_ALL)
                    self.running = False
                    await asyncio.sleep(1)
                    continue

            except Exception as e:
                self._add_log(f"{Fore.RED}ERROR in main loop: {e}{Style.RESET_ALL}")
                if self.is_test_run:
                    self.running = False # Exit on error in test mode


    async def _fetch_and_update_data(self):
        """Fetch all necessary data from the API manager and update state."""
        self._add_log("Fetching latest data from Aster DEX...")
        
        # Using asyncio.gather to fetch data and pre-cache exchange info concurrently
        results = await asyncio.gather(
            self.api_manager.analyze_current_positions(),
            self.api_manager.get_spot_account_balances(),
            self.api_manager.get_perp_account_info(),
            self.api_manager._get_spot_exchange_info(force_refresh=True),
            self.api_manager._get_perp_exchange_info(force_refresh=True),
            return_exceptions=True
        )

        # Process analysis results
        analysis_results = results[0] if isinstance(results[0], dict) else {}
        self.positions = list(analysis_results.values())

        # Process spot balances
        if isinstance(results[1], list):
            self.spot_balances = [b for b in results[1] if float(b.get('free', 0)) > 0]
            self.spot_usdt_balance = next((float(b.get('free', 0)) for b in self.spot_balances if b.get('asset') == 'USDT'), 0.0)
            
            # Fetch USD values for non-stablecoin spot balances
            stablecoins = {'USDT', 'USDC', 'USDF'}
            non_stable_balances = [b for b in self.spot_balances if b.get('asset') not in stablecoins]
            if non_stable_balances:
                price_tasks = [self.api_manager.get_spot_book_ticker(f"{b['asset']}USDT") for b in non_stable_balances]
                price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
                for i, balance in enumerate(non_stable_balances):
                    price_data = price_results[i]
                    if not isinstance(price_data, Exception) and price_data.get('bidPrice'):
                        price = float(price_data['bidPrice'])
                        balance['value_usd'] = float(balance.get('free', 0)) * price
                    else:
                        balance['value_usd'] = 0.0
        else:
            self._add_log(f"{Fore.RED}Failed to fetch spot balances: {results[1]}{Style.RESET_ALL}")

        # Process perpetual account info
        if isinstance(results[2], dict):
            perp_account_info = results[2]
            
            # Extract individual stablecoin balances from the 'assets' list
            assets = perp_account_info.get('assets', [])
            self.perp_usdt_balance = next((float(a.get('walletBalance', 0)) for a in assets if a.get('asset') == 'USDT'), 0.0)
            self.perp_usdc_balance = next((float(a.get('walletBalance', 0)) for a in assets if a.get('asset') == 'USDC'), 0.0)
            self.perp_usdf_balance = next((float(a.get('walletBalance', 0)) for a in assets if a.get('asset') == 'USDF'), 0.0)

            # Calculate the total margin balance as the sum of the stablecoin balances
            self.perp_margin_balance = self.perp_usdt_balance + self.perp_usdc_balance + self.perp_usdf_balance

            self.raw_perp_positions = [p for p in perp_account_info.get('positions', []) if float(p.get('positionAmt', 0)) != 0]

            # Fetch live mark prices for perpetual positions
            if self.raw_perp_positions:
                price_tasks = [self.api_manager.get_perp_book_ticker(p['symbol']) for p in self.raw_perp_positions]
                price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
                for i, pos in enumerate(self.raw_perp_positions):
                    price_data = price_results[i]
                    if not isinstance(price_data, Exception) and price_data.get('bidPrice'):
                        pos['markPrice'] = (float(price_data['bidPrice']) + float(price_data['askPrice'])) / 2
        else:
            self._add_log(f"{Fore.RED}Failed to fetch perp account info: {results[2]}{Style.RESET_ALL}")

        # Identify and add spot-only positions to the analysis
        symbols_with_perp = {pos.get('symbol') for pos in self.positions}
        stablecoins = {'USDT', 'USDC', 'USDF'}
        spot_only_assets = [
            {'asset': b.get('asset'), 'symbol': f"{b.get('asset')}USDT", 'balance': float(b.get('free', 0))}
            for b in self.spot_balances if b.get('asset') not in stablecoins and f"{b.get('asset')}USDT" not in symbols_with_perp
        ]
        
        if spot_only_assets:
            price_tasks = [self.api_manager.get_spot_book_ticker(asset['symbol']) for asset in spot_only_assets]
            price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
            for i, asset_info in enumerate(spot_only_assets):
                price_data = price_results[i]
                if not isinstance(price_data, Exception) and price_data.get('bidPrice'):
                    price = float(price_data['bidPrice'])
                    self.positions.append({
                        'symbol': asset_info['symbol'], 'spot_balance': asset_info['balance'], 'perp_position': 0.0,
                        'is_delta_neutral': False, 'imbalance_pct': 100.0, 'net_delta': asset_info['balance'],
                        'position_value_usd': asset_info['balance'] * price, 'leverage': 'N/A', 'current_apr': 'N/A'
                    })

        # Fetch current funding rates for delta-neutral positions
        dn_positions = [p for p in self.positions if p.get('is_delta_neutral')]
        if dn_positions:
            rate_tasks = [self.api_manager.get_funding_rate_history(p['symbol'], limit=1) for p in dn_positions]
            rate_results = await asyncio.gather(*rate_tasks, return_exceptions=True)
            for i, pos in enumerate(dn_positions):
                rate_data = rate_results[i]
                if not isinstance(rate_data, Exception) and rate_data:
                    latest_rate = float(rate_data[0].get('fundingRate', 0))
                    pos['current_apr'] = latest_rate * 3 * 365 * 100

        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._add_log("Data refresh complete.")

    def _render_dashboard(self):
        """Clears the screen and renders the entire dashboard UI."""
        os.system('cls' if os.name == 'nt' else 'clear')
        print(Style.BRIGHT + Fore.CYAN + "=" * 80)
        print(" ASTER DELTA-NEUTRAL FUNDING RATE FARMING BOT ".center(80))
        print(f" Last Updated: {self.last_updated} ".center(80))
        print("=" * 80 + Style.RESET_ALL)

        # --- Render sections here ---
        self._render_portfolio_summary()
        self._render_all_perp_positions()
        self._render_delta_neutral_positions()
        self._render_other_positions()
        self._render_spot_balances()
        self._render_opportunities()
        self._render_funding_rate_scan() # Render the on-demand scan if data is present
        self._render_logs()
        self._render_menu()

    def _get_char(self):
        """Gets a single character from standard input."""
        try: # Windows
            return msvcrt.getch().decode()
        except NameError: # Not Windows
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(sys.stdin.fileno())
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            return ch

    async def _handle_user_input(self):
        """Handles user input in a non-blocking way."""
        loop = asyncio.get_event_loop()
        while self.running:
            command = ''
            try:
                command = await loop.run_in_executor(None, self._get_char)
            except (KeyboardInterrupt, asyncio.CancelledError):
                # On Windows, Ctrl+C raises KeyboardInterrupt here.
                # Treat it as a 'q' command for graceful shutdown.
                command = 'q'
            except Exception as e:
                self._add_log(f"{Fore.RED}Input Error: {e}{Style.RESET_ALL}")
                continue

            # On non-Windows, Ctrl+C is read as the '\x03' character.
            if command == '\x03':
                command = 'q'

            command = command.strip().lower()

            if command == 'q':
                if self.running: # Ensure shutdown logic runs only once
                    self._add_log("Shutdown key pressed. Exiting...")
                    self.running = False
                    # Cancel all running tasks to allow for graceful shutdown
                    for task in asyncio.all_tasks():
                        task.cancel()
            elif command == 'r':
                self._add_log("Manual refresh requested.")
                await self._fetch_and_update_data()
                self._render_dashboard()
            elif command == 'o':
                await self._open_position_workflow()
            elif command == 'c':
                await self._close_position_workflow()
            elif command == 'f':
                await self._show_funding_rates_workflow()

    def _add_log(self, message: str):
        """Adds a timestamped message to the log queue."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_messages.append(f"[{timestamp}] {message}")

    async def _get_user_input(self, prompt: str) -> str:
        """Gets user input in a non-blocking way."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, input, prompt)

    async def _open_position_workflow(self):
        """Guides the user through opening a new delta-neutral position."""
        self._add_log("Starting 'Open Position' workflow...")
        # 1. Fetch all opportunities and their required data (prices, filters) concurrently
        all_opportunities = await self.api_manager.discover_delta_neutral_pairs()
        existing_dn_symbols = {p.get('symbol') for p in self.positions if p.get('is_delta_neutral')}
        self.opportunities = [opp for opp in all_opportunities if opp not in existing_dn_symbols]

        if not self.opportunities:
            self._add_log(f"{Fore.YELLOW}No new opportunities available to open a position.{Style.RESET_ALL}")
            self._render_dashboard()
            return
        
        self._add_log(f"Fetching data for {len(self.opportunities)} opportunities...")
        tasks = []
        for opp in self.opportunities:
            tasks.append(self.api_manager.get_spot_book_ticker(opp))
            tasks.append(self.api_manager.get_perp_symbol_filter(opp, 'MIN_NOTIONAL'))
            tasks.append(self.api_manager.get_perp_symbol_filter(opp, 'LOT_SIZE'))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        opportunity_data = {}
        for i, opp in enumerate(self.opportunities):
            price_res, min_notional_res, lot_size_res = results[i*3], results[i*3 + 1], results[i*3 + 2]
            if not isinstance(price_res, Exception) and price_res.get('bidPrice'):
                opportunity_data[opp] = {
                    'price': float(price_res['bidPrice']),
                    'min_notional_filter': min_notional_res if not isinstance(min_notional_res, Exception) else None,
                    'lot_size_filter': lot_size_res if not isinstance(lot_size_res, Exception) else None
                }

        self._render_dashboard()

        try:
            # 2. Ask user to select a symbol
            print("\n" + Fore.CYAN + "Please select a symbol to open a position (or enter 'x' to cancel):" + Style.RESET_ALL)
            for i, opp in enumerate(self.opportunities):
                price_str = f"${opportunity_data.get(opp, {}).get('price', 0):,.2f}"
                print(f"[{i+1}] {opp:<12} (Price: {price_str})")
            
            selection = await self._get_user_input("Enter the number of the symbol: ")
            if selection.strip().lower() == 'x': raise KeyboardInterrupt

            selected_symbol = self.opportunities[int(selection) - 1]
            data = opportunity_data.get(selected_symbol)
            if not data:
                self._add_log(f"{Fore.RED}Could not retrieve cached data for {selected_symbol}. Aborting.{Style.RESET_ALL}")
                return
            spot_price = data['price']

            # 3. Pre-validate available capital
            max_capital = min(self.spot_usdt_balance, self.perp_margin_balance)
            min_notional_from_filter = float(data['min_notional_filter'].get('notional', 0)) if data['min_notional_filter'] else 0.0
            min_qty_from_filter = float(data['lot_size_filter'].get('minQty', 0)) if data['lot_size_filter'] else 0.0
            min_notional_from_qty = min_qty_from_filter * spot_price
            true_min_notional = max(min_notional_from_filter, min_notional_from_qty)

            if true_min_notional > 0 and max_capital < true_min_notional:
                self._add_log(f"{Fore.RED}Error: Insufficient capital for {selected_symbol}. Have ${max_capital:.2f}, need minimum ${true_min_notional:.2f}.{Style.RESET_ALL}")
                return

            # 4. Ask for capital
            prompt = f"Enter USD capital for {selected_symbol} (max: ${max_capital:.2f}, or 'x' to cancel): "
            if true_min_notional > 0:
                prompt = f"Enter USD capital for {selected_symbol} (min: ${true_min_notional:.2f}, max: ${max_capital:.2f}, or 'x' to cancel): "
            
            capital_str = await self._get_user_input(prompt)
            if capital_str.strip().lower() == 'x': raise KeyboardInterrupt

            capital_to_deploy = float(capital_str)
            if capital_to_deploy > max_capital or (true_min_notional > 0 and capital_to_deploy < true_min_notional):
                self._add_log(f"{Fore.RED}Error: Invalid amount. Please enter a value between ${true_min_notional:.2f} and ${max_capital:.2f}.{Style.RESET_ALL}")
                return

            # 5. Calculate position size
            base_asset = selected_symbol.replace('USDT', '')
            existing_spot_quantity = sum(float(b.get('free', '0')) for b in self.spot_balances if b.get('asset') == base_asset)
            existing_spot_usd = existing_spot_quantity * spot_price

            sizing = self.logic.calculate_position_size(
                total_usd_capital=capital_to_deploy,
                spot_price=spot_price,
                existing_spot_usd=existing_spot_usd
            )
            
            # 6. Show confirmation
            print("\n" + Fore.YELLOW + "--- CONFIRMATION ---" + Style.RESET_ALL)
            print(f"Symbol: {selected_symbol}, Total Value: ${capital_to_deploy:.2f}")
            print(f"Action: Open delta-neutral position via MARKET orders.")
            if sizing['existing_spot_usd_utilized'] > 0:
                print(Fore.CYAN + f"Utilizing Existing Spot: ${sizing['existing_spot_usd_utilized']:.2f}" + Style.RESET_ALL)
            print(f"New Spot BUY: ${sizing['new_spot_capital_required']:.2f} (Qty: {sizing['spot_quantity_to_buy']:.6f})")
            print(f"Perp SELL: ${sizing['perp_capital_required']:.2f} (Qty: {sizing['total_perp_quantity_to_short']:.6f})")
            
            confirm = await self._get_user_input("Press Enter to confirm (or enter 'x' to cancel): ")
            if confirm.strip().lower() == 'x' or confirm.strip() != '':
                self._add_log("Trade execution cancelled by user.")
                return
                
            # 7. Execute trades
            self._add_log("Executing trades...")
            if sizing['new_spot_capital_required'] > 1.0:
                await self.api_manager.place_spot_buy_market_order(symbol=selected_symbol, quote_quantity=str(sizing['new_spot_capital_required']))
            await self.api_manager.place_perp_market_order(symbol=selected_symbol, quantity=str(sizing['total_perp_quantity_to_short']), side='SELL')
            self._add_log(f"{Fore.GREEN}Successfully opened position for {selected_symbol}.{Style.RESET_ALL}")

        except (ValueError, IndexError):
            self._add_log(f"{Fore.RED}Invalid selection.{Style.RESET_ALL}")
        except KeyboardInterrupt:
            self._add_log("Operation cancelled by user.")
        finally:
            self._render_dashboard()

    async def _close_position_workflow(self):
        """Guides the user through closing an existing delta-neutral position."""
        self._add_log("Starting 'Close Position' workflow...")
        dn_positions = [p for p in self.positions if p.get('is_delta_neutral')]

        if not dn_positions:
            self._add_log(f"{Fore.YELLOW}No delta-neutral positions available to close.{Style.RESET_ALL}")
            self._render_dashboard()
            return

        try:
            # 1. Ask user to select a position
            print("\n" + Fore.CYAN + "Select a delta-neutral position to close (or 'x' to cancel):" + Style.RESET_ALL)
            for i, pos in enumerate(dn_positions):
                print(f"[{i+1}] {pos.get('symbol', 'N/A')}")
            
            selection = await self._get_user_input("Enter the number of the position: ")
            if selection.strip().lower() == 'x': raise KeyboardInterrupt

            selected_position = dn_positions[int(selection) - 1]
            symbol_to_close = selected_position.get('symbol')

            # 2. Show confirmation
            print("\n" + Fore.YELLOW + "--- CONFIRMATION ---" + Style.RESET_ALL)
            print(f"Symbol: {symbol_to_close}")
            print(f"Spot Balance: {selected_position.get('spot_balance', 0):.6f}")
            print(f"Perp Position: {selected_position.get('perp_position', 0):.6f}")
            
            confirm = await self._get_user_input(f"Press Enter to confirm closing the {symbol_to_close} position (or 'x' to cancel): ")
            if confirm.strip().lower() == 'x' or confirm.strip() != '':
                self._add_log("Close position cancelled by user.")
                return

            # 3. Execute closing trades
            self._add_log(f"Closing position for {symbol_to_close}...")
            perp_quantity = abs(selected_position.get('perp_position', 0))
            side_to_close = 'BUY' if selected_position.get('perp_position', 0) < 0 else 'SELL'
            
            await self.api_manager.close_perp_position(symbol=symbol_to_close, quantity=str(perp_quantity), side_to_close=side_to_close)
            await self.api_manager.place_spot_sell_market_order(symbol=symbol_to_close, base_quantity=str(selected_position.get('spot_balance', 0)))
            self._add_log(f"{Fore.GREEN}Successfully closed position for {symbol_to_close}.{Style.RESET_ALL}")

        except (ValueError, IndexError):
            self._add_log(f"{Fore.RED}Invalid selection.{Style.RESET_ALL}")
        except KeyboardInterrupt:
            self._add_log("Operation cancelled by user.")
        finally:
            self._render_dashboard()

    async def _show_funding_rates_workflow(self):
        """Fetches and displays funding rates for top perpetual contracts."""
        self._add_log("Fetching top funding rates for delta-neutral pairs...")
        try:
            # Dynamically discover pairs available in both spot and perp markets
            self._add_log("Discovering tradable pairs...")
            spot_symbols = await self.api_manager.get_available_spot_symbols()
            perp_symbols = await self.api_manager.get_available_perp_symbols()
            
            if not spot_symbols or not perp_symbols:
                self._add_log(f"{Fore.YELLOW}Could not retrieve symbol lists from one or both markets.{Style.RESET_ALL}")
                return

            # Find the intersection of the two lists
            symbols_to_scan = sorted(list(set(spot_symbols) & set(perp_symbols)))
            
            if not symbols_to_scan:
                self._add_log(f"{Fore.YELLOW}No symbols are currently available in both spot and perpetual markets.{Style.RESET_ALL}")
                return

            self._add_log(f"Found {len(symbols_to_scan)} pairs available for delta-neutral. Fetching rates...")

            rate_tasks = [self.api_manager.get_funding_rate_history(s, limit=1) for s in symbols_to_scan]
            rate_results = await asyncio.gather(*rate_tasks, return_exceptions=True)

            funding_data = []
            for i, symbol in enumerate(symbols_to_scan):
                rate_data = rate_results[i]
                if not isinstance(rate_data, Exception) and rate_data:
                    rate = float(rate_data[0].get('fundingRate', 0))
                    apr = rate * 3 * 365 * 100
                    funding_data.append({'symbol': symbol, 'rate': rate, 'apr': apr})
            
            # Sort by highest APR
            self.funding_rate_cache = sorted(funding_data, key=lambda x: x['apr'], reverse=True)
            self._add_log("Funding rate scan complete.")

        except Exception as e:
            self._add_log(f"{Fore.RED}Error during funding rate scan: {e}{Style.RESET_ALL}")
        
        self._render_dashboard() # Re-render to show the new data

    def _render_portfolio_summary(self):
        """Renders the summary of portfolio balances."""
        print(Fore.GREEN + "\n--- Portfolio Summary ---" + Style.RESET_ALL)
        total_portfolio_value = self.perp_margin_balance + self.spot_usdt_balance
        print(f"  Perp Margin Balance: ${self.perp_margin_balance:,.2f} (USDT: {self.perp_usdt_balance:.2f}, USDC: {self.perp_usdc_balance:.2f}, USDF: {self.perp_usdf_balance:.2f})")
        print(f"  Spot USDT Balance:   ${self.spot_usdt_balance:,.2f}")
        print(f"  Total Portfolio USD: ${total_portfolio_value:,.2f}")

    def _render_all_perp_positions(self):
        """Renders a detailed view of all raw perpetual positions."""
        print(Fore.GREEN + "\n--- All Perpetual Positions (Raw) ---" + Style.RESET_ALL)
        if not self.raw_perp_positions:
            print("  No open perpetual positions.")
            return
        
        header = f"  {'Symbol':<12} {'Amount':>12} {'Entry Price':>15} {'Mark Price':>15} {'Unrealized PNL':>18}"
        print(header)
        print("  " + "-" * len(header))

        for pos in self.raw_perp_positions:
            amount = float(pos.get('positionAmt', 0))
            entry_price = float(pos.get('entryPrice', 0))
            mark_price = float(pos.get('markPrice', entry_price)) # Use entry if mark not fetched
            pnl = (mark_price - entry_price) * amount if amount != 0 else 0
            
            pnl_color = Fore.GREEN if pnl >= 0 else Fore.RED
            print(f"  {pos.get('symbol', 'N/A'):<12} {amount:>12.6f} {entry_price:>15,.4f} {mark_price:>15,.4f} {pnl_color}{pnl:>18,.2f}{Style.RESET_ALL}")

    def _render_delta_neutral_positions(self):
        """Renders the analyzed delta-neutral positions."""
        print(Fore.GREEN + "\n--- Delta-Neutral Positions ---" + Style.RESET_ALL)
        
        dn_positions = [p for p in self.positions if p.get('is_delta_neutral')]
        
        if not dn_positions:
            print("  No delta-neutral positions found.")
            return

        header = f"  {'Symbol':<12} {'Spot Balance':>15} {'Perp Position':>15} {'Net Delta':>12} {'Value (USD)':>15} {'Imbalance':>12} {'APR (%)':>10}"
        print(header)
        print("  " + "-" * len(header))

        for pos in dn_positions:
            symbol = pos.get('symbol', 'N/A')
            spot_balance = pos.get('spot_balance', 0.0)
            perp_position = pos.get('perp_position', 0.0)
            net_delta = pos.get('net_delta', 0.0)
            value_usd = pos.get('position_value_usd', 0.0)
            imbalance = pos.get('imbalance_pct', 0.0)
            apr = pos.get('current_apr', 'N/A')
            apr_str = f"{apr:.2f}" if isinstance(apr, (int, float)) else str(apr)

            print(Fore.CYAN + f"  {symbol:<12} {spot_balance:>15.6f} {perp_position:>15.6f} {net_delta:>12.6f} {value_usd:>15,.2f} {imbalance:>11.2f}% {apr_str:>10}" + Style.RESET_ALL)

    def _render_other_positions(self):
        """Renders non-delta-neutral positions, like spot-only holdings or imbalanced pairs."""
        print(Fore.GREEN + "\n--- Other Holdings (Non-Delta-Neutral) ---" + Style.RESET_ALL)
        
        other_positions = [p for p in self.positions if not p.get('is_delta_neutral')]

        if not other_positions:
            print("  No other holdings found.")
            return

        header = f"  {'Symbol':<12} {'Spot Balance':>15} {'Perp Position':>15} {'Net Delta':>12} {'Value (USD)':>15} {'Imbalance':>12}"
        print(header)
        print("  " + "-" * len(header))

        for pos in other_positions:
            symbol = pos.get('symbol', 'N/A')
            spot_balance = pos.get('spot_balance', 0.0)
            perp_position = pos.get('perp_position', 0.0)
            net_delta = pos.get('net_delta', 0.0)
            value_usd = pos.get('position_value_usd', 0.0)
            imbalance = pos.get('imbalance_pct', 0.0)

            print(Fore.YELLOW + f"  {symbol:<12} {spot_balance:>15.6f} {perp_position:>15.6f} {net_delta:>12.6f} {value_usd:>15,.2f} {imbalance:>11.2f}%" + Style.RESET_ALL)

    def _render_spot_balances(self):
        """Renders non-stablecoin spot balances including their USD value."""
        print(Fore.GREEN + "\n--- Spot Balances (Excluding Stables) ---" + Style.RESET_ALL)
        
        non_stable_balances = [b for b in self.spot_balances if b.get('asset') not in ['USDT', 'USDC', 'USDF']]
        
        if not non_stable_balances:
            print("  No significant non-stablecoin spot balances found.")
            return

        header = f"  {'Asset':<10} {'Free':>15} {'Locked':>15} {'Value (USD)':>18}"
        print(header)
        print("  " + "-" * (len(header) + 4))

        for balance in non_stable_balances:
            asset = balance.get('asset', 'N/A')
            free = float(balance.get('free', 0))
            locked = float(balance.get('locked', 0))
            value_usd = balance.get('value_usd', 0.0)
            print(f"  {asset:<10} {free:>15.6f} {locked:>15.6f} {value_usd:>18,.2f}")

    def _render_opportunities(self):
        """Renders potential opportunities for new positions."""
        if not self.opportunities:
            return # Do not render the section if there are no opportunities
        
        print(Fore.GREEN + "\n--- Potential Opportunities ---" + Style.RESET_ALL)
        for opp in self.opportunities:
            print(f"  - {opp}")

    def _render_funding_rate_scan(self):
        """Renders the results of an on-demand funding rate scan."""
        if not self.funding_rate_cache:
            return # Don't render if no scan has been run

        print(Fore.GREEN + "\n--- Top Funding Rate APRs (On-Demand Scan) ---" + Style.RESET_ALL)
        header = f"  {'Symbol':<12} {'Current Rate':>15} {'Annualized APR (%)':>20}"
        print(header)
        print("  " + "-" * len(header))

        for item in self.funding_rate_cache:
            rate = item.get('rate', 0)
            apr = item.get('apr', 0)
            apr_color = Fore.GREEN if apr > 0 else Fore.RED
            print(f"  {item.get('symbol', 'N/A'):<12} {rate:>15.6f} {apr_color}{apr:>20.2f}{Style.RESET_ALL}")

    def _render_logs(self):
        """Renders the most recent log messages."""
        print(Fore.GREEN + "\n--- Logs ---" + Style.RESET_ALL)
        if not self.log_messages:
            print("  No log messages.")
            return
        
        for msg in self.log_messages:
            # The message should already have color codes if needed
            print(f"  {msg}")

    def _render_menu(self):
        """Renders the main menu of available actions."""
        print(Fore.CYAN + "\n--- Menu ---" + Style.RESET_ALL)
        print("  [R] Refresh Data   [O] Open Position   [C] Close Position   [F] Scan Funding Rates   [Q] Quit")



def main():
    """The main function to run the bot."""
    parser = argparse.ArgumentParser(description="Delta-Neutral Funding Rate Farming Bot")
    parser.add_argument('--test', action='store_true', help="Run in test mode (fetch, render once, then exit)")
    args = parser.parse_args()

    # Check for required environment variables
    required_vars = ['API_USER', 'API_SIGNER', 'API_PRIVATE_KEY', 'APIV1_PUBLIC_KEY', 'APIV1_PRIVATE_KEY']
    if not all(os.getenv(var) for var in required_vars):
        print(Fore.RED + "ERROR: Not all required environment variables are set in your .env file." + Style.RESET_ALL)
        print("Please ensure API_USER, API_SIGNER, API_PRIVATE_KEY, APIV1_PUBLIC_KEY, and APIV1_PRIVATE_KEY are configured.")
        sys.exit(1)

    app = DashboardApp(is_test_run=args.test)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\nCaught KeyboardInterrupt, shutting down...")

if __name__ == '__main__':
    main()
