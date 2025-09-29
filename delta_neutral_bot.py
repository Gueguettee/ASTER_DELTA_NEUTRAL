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
import math
from decimal import Decimal

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
        self.interactive_mode = False  # Flag to pause auto-refresh during user interactions
        self.is_standalone_workflow = False

        # State variables to hold dashboard data
        self.last_updated = "Never"
        self.portfolio_value = 0.0
        self.positions = []
        self.spot_balances = []
        self.opportunities = []
        self.log_messages = deque(maxlen=5)  # Store last 5 log messages
        self.perp_margin_balance = 0.0
        self.perp_usdt_balance = 0.0
        self.perp_usdc_balance = 0.0
        self.perp_usdf_balance = 0.0
        self.spot_usdt_balance = 0.0
        self.raw_perp_positions = []
        self.funding_rate_cache = None  # To hold on-demand funding rate scan results

    def _truncate(self, value: float, precision: int) -> float:
        """Truncates a float to a given precision without rounding."""
        if precision < 0:
            precision = 0
        if precision == 0:
            return math.floor(value)
        factor = 10.0 ** precision
        return math.floor(value * factor) / factor

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

            # Skip refresh if in interactive mode (user is in a workflow)
            if self.interactive_mode:
                continue

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
        self._add_log("Fetching latest portfolio data from Aster DEX...")
        
        portfolio_data = await self.api_manager.get_comprehensive_portfolio_data()

        if not portfolio_data:
            self._add_log(f"{Fore.RED}Failed to fetch comprehensive portfolio data.{Style.RESET_ALL}")
            return

        # Assign data from the comprehensive payload to the dashboard state
        self.positions = portfolio_data.get('analyzed_positions', [])
        self.spot_balances = portfolio_data.get('spot_balances', [])
        self.raw_perp_positions = portfolio_data.get('raw_perp_positions', [])
        perp_account_info = portfolio_data.get('perp_account_info', {})

        # Extract summary values from the fetched data
        self.spot_usdt_balance = next((float(b.get('free', 0)) for b in self.spot_balances if b.get('asset') == 'USDT'), 0.0)
        
        assets = perp_account_info.get('assets', [])
        self.perp_usdt_balance = next((float(a.get('walletBalance', 0)) for a in assets if a.get('asset') == 'USDT'), 0.0)
        self.perp_usdc_balance = next((float(a.get('walletBalance', 0)) for a in assets if a.get('asset') == 'USDC'), 0.0)
        self.perp_usdf_balance = next((float(a.get('walletBalance', 0)) for a in assets if a.get('asset') == 'USDF'), 0.0)
        self.perp_margin_balance = self.perp_usdt_balance + self.perp_usdc_balance + self.perp_usdf_balance

        # Fetch available opportunities for new positions
        try:
            all_opportunities = await self.api_manager.discover_delta_neutral_pairs()
            existing_dn_symbols = {p.get('symbol') for p in self.positions if p.get('is_delta_neutral')}
            self.opportunities = [opp for opp in all_opportunities if opp not in existing_dn_symbols]
        except Exception as e:
            self._add_log(f"Could not fetch opportunities: {e}")
            self.opportunities = []

        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._add_log("Data refresh complete.")

    def _render_dashboard(self):
        """Clears the screen and renders the entire dashboard UI."""
        # Skip rendering if in interactive mode to avoid clearing user prompts
        if self.interactive_mode:
            return

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
                self.interactive_mode = True
                await self._open_position_workflow()
                self.interactive_mode = False
                self._render_dashboard()
            elif command == 'c':
                self.interactive_mode = True
                await self._close_position_workflow()
                self.interactive_mode = False
                self._render_dashboard()
            elif command == 'f':
                self.interactive_mode = True
                await self._show_funding_rates_workflow()
                self.interactive_mode = False
                self._render_dashboard()
            elif command == 'h':
                self.interactive_mode = True
                await self._perform_health_check()
                self.interactive_mode = False
                self._render_dashboard()
            elif command == 'b':
                self.interactive_mode = True
                await self._rebalance_usdt_workflow()
                self.interactive_mode = False
                self._render_dashboard()

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
        try:
            # 1. Select Symbol
            all_opportunities = await self.api_manager.discover_delta_neutral_pairs()
            if not all_opportunities:
                self._add_log(f"{Fore.YELLOW}No new opportunities available to open a position.{Style.RESET_ALL}")
                return

            print("\n" + Fore.CYAN + "Please select a symbol to open a position (or enter 'x' to cancel):" + Style.RESET_ALL)
            for i, opp in enumerate(all_opportunities):
                print(f"[{i+1}] {opp}")
            
            selection = await self._get_user_input("Enter the number of the symbol: ")
            if selection.strip().lower() == 'x': raise KeyboardInterrupt
            selected_symbol = all_opportunities[int(selection) - 1]

            # 2. Get Capital Input & Calculate Minimums
            perp_balance = self.perp_margin_balance
            spot_balance = self.spot_usdt_balance
            max_capital = min(spot_balance, perp_balance)

            # Fetch filters to calculate true minimum notional
            min_notional_filter, lot_size_filter, spot_price_data = await asyncio.gather(
                self.api_manager.get_perp_symbol_filter(selected_symbol, 'MIN_NOTIONAL'),
                self.api_manager.get_perp_symbol_filter(selected_symbol, 'LOT_SIZE'),
                self.api_manager.get_spot_book_ticker(selected_symbol)
            )
            spot_price = float(spot_price_data['bidPrice'])

            min_notional_from_filter = float(min_notional_filter.get('notional', 0)) if min_notional_filter else 0.0
            min_qty_from_filter = float(lot_size_filter.get('minQty', 0)) if lot_size_filter else 0.0
            min_notional_from_qty = min_qty_from_filter * spot_price
            true_min_notional = max(min_notional_from_filter, min_notional_from_qty)

            # Add a small buffer and round up for a clean user prompt
            display_min_notional = math.ceil(true_min_notional + 1.0)

            prompt = f"Enter USD capital for {selected_symbol} (min: ${display_min_notional:.2f}, max: ${max_capital:.2f}, or 'x' to cancel): "
            capital_str = await self._get_user_input(prompt)
            if capital_str.strip().lower() == 'x': raise KeyboardInterrupt
            capital_to_deploy = float(capital_str)

            if capital_to_deploy > max_capital or capital_to_deploy < true_min_notional:
                self._add_log(f"{Fore.RED}Error: Invalid amount. Please enter a value between ${true_min_notional:.2f} and ${max_capital:.2f}.{Style.RESET_ALL}")
                return

            # 3. Perform Dry Run to get trade details
            self._add_log("Calculating trade details (dry run)...")
            trade_plan = await self.api_manager.prepare_and_execute_dn_position(selected_symbol, capital_to_deploy, dry_run=True)

            if not trade_plan.get('success'):
                error_message = f"{Fore.RED}Could not create trade plan: {trade_plan.get('message')}{Style.RESET_ALL}"
                self._add_log(error_message)
                if self.is_standalone_workflow:
                    print(error_message)
                return

            # 4. Show Confirmation
            details = trade_plan['details']
            base_asset = selected_symbol.replace('USDT', '')
            print("\n" + Fore.YELLOW + "--- CONFIRMATION (ADJUSTED FOR LOT SIZE) ---" + Style.RESET_ALL)
            print(f"Symbol: {details['symbol']}, Initial Capital: ${details['capital_to_deploy']:.2f}")
            print(f"Action: Open delta-neutral position via MARKET orders at 1x leverage.")
            print(Fore.MAGENTA + f"Perp Lot Size Filter (stepSize): {details['lot_size_filter'].get('stepSize') if details['lot_size_filter'] else 'N/A'}" + Style.RESET_ALL)
            print(f"Ideal Perp Qty:   {details['ideal_perp_qty']:.8f}")
            print(f"Final Perp Qty:   {details['final_perp_qty']:.8f}")
            print("-"*40)
            if (details['existing_spot_quantity'] * details['spot_price']) > 0:
                print(Fore.CYAN + f"Utilizing Existing Spot: {details['existing_spot_quantity']:.8f} {base_asset} (${(details['existing_spot_quantity'] * details['spot_price']):.2f})" + Style.RESET_ALL)
            print(f"Spot BUY Qty: {details['spot_qty_to_buy']:.8f} (${details['spot_capital_to_buy']:.2f})")
            print(f"Perp SELL Qty: {details['final_perp_qty']:.8f} (${details['final_perp_qty'] * details['spot_price']:.2f})")

            confirm = await self._get_user_input("Press Enter to confirm (or enter 'x' to cancel): ")
            if confirm.strip().lower() == 'x' or confirm.strip() != '':
                self._add_log("Trade execution cancelled by user.")
                return

            # 5. Execute Trade
            self._add_log("Executing trades...")
            exec_result = await self.api_manager.prepare_and_execute_dn_position(selected_symbol, capital_to_deploy, dry_run=False)

            if exec_result.get('success'):
                success_message = f"{Fore.GREEN}{exec_result.get('message')}{Style.RESET_ALL}"
                self._add_log(success_message)
                if self.is_standalone_workflow:
                    print(success_message)
            else:
                error_message = f"{Fore.RED}Execution failed: {exec_result.get('message')}{Style.RESET_ALL}"
                self._add_log(error_message)
                if self.is_standalone_workflow:
                    print(error_message)

            # Wait and refresh data
            self._add_log("Refreshing data to show new position...")
            await asyncio.sleep(1)
            await self._fetch_and_update_data()

        except (ValueError, IndexError):
            self._add_log(f"{Fore.RED}Invalid selection.{Style.RESET_ALL}")
        except KeyboardInterrupt:
            self._add_log("Operation cancelled by user.")

    async def _close_position_workflow(self):
        """Guides the user through closing an existing delta-neutral position."""
        self._add_log("Starting 'Close Position' workflow...")
        dn_positions = [p for p in self.positions if p.get('is_delta_neutral')]

        if not dn_positions:
            message = f"{Fore.YELLOW}No delta-neutral positions available to close.{Style.RESET_ALL}"
            self._add_log(message)
            if self.is_standalone_workflow:
                print(message)
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

            # 3. Execute closing trades via the centralized method
            self._add_log(f"Closing position for {symbol_to_close}...")
            close_result = await self.api_manager.execute_dn_position_close(symbol_to_close)

            if close_result.get('success'):
                success_message = f"{Fore.GREEN}{close_result.get('message')}{Style.RESET_ALL}"
                self._add_log(success_message)
                if self.is_standalone_workflow:
                    print(success_message)
            else:
                error_message = f"{Fore.RED}Failed to close position: {close_result.get('message')}{Style.RESET_ALL}"
                self._add_log(error_message)
                if self.is_standalone_workflow:
                    print(error_message)

            # Wait 1 second then refresh data to show the closed position
            self._add_log("Refreshing data to show position closure...")
            await asyncio.sleep(1)
            await self._fetch_and_update_data()

        except (ValueError, IndexError):
            self._add_log(f"{Fore.RED}Invalid selection.{Style.RESET_ALL}")
        except KeyboardInterrupt:
            self._add_log("Operation cancelled by user.")

    async def _show_funding_rates_workflow(self):
        """Fetches and displays funding rates for top perpetual contracts."""
        self._add_log("Fetching top funding rates for delta-neutral pairs...")
        try:
            self.funding_rate_cache = await self.api_manager.get_all_funding_rates()
            self._add_log("Funding rate scan complete.")

            # Display the results to the user
            if self.funding_rate_cache:
                print(f"\n{Fore.GREEN}=== FUNDING RATES SCAN RESULTS ==={Style.RESET_ALL}")
                render_funding_rates_table(self.funding_rate_cache)
                print(f"\n{Fore.CYAN}Press Enter to return to dashboard...{Style.RESET_ALL}")
                await self._get_user_input("")
            else:
                print(f"\n{Fore.YELLOW}No funding rate data available.{Style.RESET_ALL}")
                print(f"\n{Fore.CYAN}Press Enter to return to dashboard...{Style.RESET_ALL}")
                await self._get_user_input("")

        except Exception as e:
            self._add_log(f"{Fore.RED}Error during funding rate scan: {e}{Style.RESET_ALL}")
            print(f"\n{Fore.CYAN}Press Enter to return to dashboard...{Style.RESET_ALL}")
            await self._get_user_input("")

    async def _rebalance_usdt_workflow(self):
        """Guides the user through rebalancing USDT between spot and perpetual accounts."""
        self._add_log("Starting USDT rebalance workflow...")

        try:
            # Get current balances and calculate rebalance need
            self._add_log("Analyzing current USDT distribution...")
            result = await self.api_manager.rebalance_usdt_50_50()

            # Display current state
            print("\n" + Fore.CYAN + "=== USDT BALANCE ANALYSIS ===" + Style.RESET_ALL)
            print(f"Current Spot USDT:     ${result['current_spot_usdt']:>10.2f}")
            print(f"Current Perp USDT:     ${result['current_perp_usdt']:>10.2f}")
            print(f"Total Available USDT:  ${result['total_usdt']:>10.2f}")
            print(f"Target Each (50/50):   ${result['target_each']:>10.2f}")

            if not result['transfer_needed']:
                print(Fore.GREEN + "\n✓ ALREADY BALANCED: Your USDT is already distributed 50/50 (within $1)" + Style.RESET_ALL)
                print(f"\n{Fore.CYAN}Press Enter to return to dashboard...{Style.RESET_ALL}")
                await self._get_user_input("")
                return

            # Show transfer details
            print(f"\nTransfer Required:")
            print(f"  Amount:     ${result['transfer_amount']:.2f}")
            print(f"  Direction:  {result['transfer_direction'].replace('_', ' → ')}")

            if result['transfer_direction'] == 'SPOT_TO_PERP':
                print(f"  Action:     Move ${result['transfer_amount']:.2f} from Spot to Perpetual")
            else:
                print(f"  Action:     Move ${result['transfer_amount']:.2f} from Perpetual to Spot")

            # Ask for confirmation
            print("\n" + Fore.YELLOW + "--- CONFIRMATION ---" + Style.RESET_ALL)
            print(f"Execute USDT rebalance transfer?")
            print(f"This will move ${result['transfer_amount']:.2f} USDT between your accounts.")

            confirm = await self._get_user_input("Press Enter to confirm transfer (or 'x' to cancel): ")
            if confirm.strip().lower() == 'x' or confirm.strip() != '':
                self._add_log("USDT rebalance cancelled by user.")
                return

            # Execute the rebalance (which includes the transfer)
            self._add_log("Executing USDT rebalance transfer...")
            final_result = await self.api_manager.rebalance_usdt_50_50()

            if final_result.get('transfer_result'):
                transfer_result = final_result['transfer_result']
                if transfer_result.get('status') == 'SUCCESS':
                    self._add_log(f"{Fore.GREEN}USDT rebalance completed successfully!{Style.RESET_ALL}")
                    print(f"\n{Fore.GREEN}✓ TRANSFER SUCCESSFUL{Style.RESET_ALL}")
                    print(f"Transaction ID: {transfer_result.get('tranId')}")
                    print(f"Status: {transfer_result.get('status')}")
                else:
                    self._add_log(f"{Fore.RED}Transfer failed: {transfer_result}{Style.RESET_ALL}")
            else:
                self._add_log(f"{Fore.RED}No transfer result received{Style.RESET_ALL}")

            # Wait 1 second then refresh data to show the new balances
            self._add_log("Refreshing data to show updated balances...")
            await asyncio.sleep(1)
            await self._fetch_and_update_data()

            print(f"\n{Fore.CYAN}Rebalance complete. Press Enter to return to dashboard...{Style.RESET_ALL}")
            await self._get_user_input("")

        except (ValueError, KeyError) as e:
            self._add_log(f"{Fore.RED}Rebalance error: {e}{Style.RESET_ALL}")
            print(f"\n{Fore.CYAN}Press Enter to return to dashboard...{Style.RESET_ALL}")
            await self._get_user_input("")
        except KeyboardInterrupt:
            self._add_log("USDT rebalance cancelled by user.")
            print(f"\n{Fore.CYAN}Press Enter to return to dashboard...{Style.RESET_ALL}")
            await self._get_user_input("")
        except Exception as e:
            self._add_log(f"{Fore.RED}Unexpected error during rebalance: {e}{Style.RESET_ALL}")
            print(f"\n{Fore.CYAN}Press Enter to return to dashboard...{Style.RESET_ALL}")
            await self._get_user_input("")

    async def _perform_health_check(self):
        """Performs a health check on all positions and displays warnings."""
        self._add_log("Performing portfolio health check...")

        try:
            # Use shared health check logic
            health_issues, critical_issues, dn_positions_count, position_pnl_data = await perform_health_check_analysis(self.api_manager)

            if dn_positions_count == 0:
                self._add_log(f"{Fore.YELLOW}No delta-neutral positions found to check.{Style.RESET_ALL}")
                return

            # Display health check results
            print("\n" + Fore.CYAN + "=== PORTFOLIO HEALTH CHECK ===" + Style.RESET_ALL)
            print(Fore.CYAN + "Health Check Criteria:" + Style.RESET_ALL)
            print(f"  {Fore.GREEN}Spot USD > $10{Style.RESET_ALL}: Healthy")
            print(f"  {Fore.YELLOW}Spot USD < $10{Style.RESET_ALL}: Warning (rebalancing advised)")
            print(f"  {Fore.RED}Spot USD < $5{Style.RESET_ALL}: Critical (impossible to close)")
            print(f"  {Fore.YELLOW}Short PnL < -25%{Style.RESET_ALL}: Warning")
            print(f"  {Fore.RED}Short PnL < -50%{Style.RESET_ALL}: Critical")

            if critical_issues:
                print(Fore.RED + "\nCRITICAL ISSUES:" + Style.RESET_ALL)
                for issue in critical_issues:
                    print(Fore.RED + f"  {issue}" + Style.RESET_ALL)

            if health_issues:
                print(Fore.YELLOW + "\nWARNINGS:" + Style.RESET_ALL)
                for issue in health_issues:
                    print(Fore.YELLOW + f"  {issue}" + Style.RESET_ALL)

            # Display position PnL summary
            if position_pnl_data:
                print(Fore.CYAN + "\nPOSITION PnL SUMMARY:" + Style.RESET_ALL)
                header = f"{'Symbol':<12} {'Value':<12} {'Imbalance':<10} {'PnL %':<10}"
                print(header)
                print("-" * len(header))

                for pos_data in position_pnl_data:
                    symbol = pos_data['symbol']
                    value_usd = pos_data['position_value_usd']
                    spot_value_usd = pos_data['spot_value_usd']
                    imbalance_pct = pos_data['imbalance_pct']
                    pnl_pct = pos_data['pnl_pct']

                    # Color code based on spot value thresholds
                    if spot_value_usd < 5:
                        row_color = Fore.RED  # Critical
                    elif spot_value_usd < 10:
                        row_color = Fore.YELLOW  # Warning
                    else:
                        row_color = Fore.GREEN  # Healthy

                    # Color code PnL based on performance
                    if pnl_pct is not None:
                        if pnl_pct >= -25:  # Good PnL (above -25%)
                            pnl_color = Fore.GREEN
                            pnl_str = f"{pnl_pct:+.2f}%"
                        else:  # Bad PnL (below -25%, already in warnings/critical)
                            pnl_color = Fore.RED
                            pnl_str = f"{pnl_pct:+.2f}%"
                    else:
                        pnl_color = Fore.YELLOW
                        pnl_str = "N/A"

                    print(f"{row_color}{symbol:<12} ${value_usd:<11.2f} {imbalance_pct:<9.2f}% {pnl_color}{pnl_str:<10}{Style.RESET_ALL}")

            if not critical_issues and not health_issues:
                print(Fore.GREEN + "\nALL CLEAR: No health issues detected with your positions." + Style.RESET_ALL)
            else:
                print(f"\n{Fore.CYAN}RECOMMENDATION:{Style.RESET_ALL}")
                if critical_issues:
                    print(f"{Fore.RED}  URGENT: Critical issues detected. Consider immediate rebalancing or position closure.{Style.RESET_ALL}")
                else:
                    print(f"{Fore.YELLOW}  Consider rebalancing your positions to address the warnings above.{Style.RESET_ALL}")
                print(f"{Fore.CYAN}  Use 'r' in the dashboard or run: python delta_neutral_bot.py --rebalance{Style.RESET_ALL}")

            print(f"\n{Fore.CYAN}Health check complete. Press Enter to return to dashboard...{Style.RESET_ALL}")
            await self._get_user_input("")

        except Exception as e:
            self._add_log(f"{Fore.RED}Error during health check: {e}{Style.RESET_ALL}")

    def _render_portfolio_summary(self):
        """Renders the summary of portfolio balances."""
        print()  # Single line break before section
        render_portfolio_summary(
            self.perp_usdt_balance,
            self.perp_usdc_balance,
            self.perp_usdf_balance,
            self.spot_usdt_balance,
            title="Portfolio Summary",
            indent=""
        )

    def _render_all_perp_positions(self):
        """Renders a detailed view of all raw perpetual positions."""
        if not self.raw_perp_positions:
            print(Fore.GREEN + "\n--- All Perpetual Positions (Raw) ---" + Style.RESET_ALL)
            print("  No open perpetual positions.")
            return

        # Enhance position data with calculated fields for the common function
        enhanced_positions = []
        for pos in self.raw_perp_positions:
            position_amt = float(pos.get('positionAmt', 0))
            entry_price = float(pos.get('entryPrice', 0))
            mark_price = float(pos.get('markPrice', entry_price))
            leverage = float(pos.get('leverage', 1))

            # Calculate PnL percentage
            if entry_price > 0:
                if position_amt > 0:  # Long position
                    pnl_pct = ((mark_price - entry_price) / entry_price) * 100
                else:  # Short position
                    pnl_pct = ((entry_price - mark_price) / entry_price) * 100
            else:
                pnl_pct = 0.0

            # Calculate notional value and unrealized PnL
            notional_value = abs(position_amt) * mark_price
            unrealized_pnl = float(pos.get('unrealizedProfit', 0))

            # If unrealizedProfit is not available, calculate it
            if unrealized_pnl == 0 and position_amt != 0:
                unrealized_pnl = (mark_price - entry_price) * position_amt

            # Create enhanced position with calculated fields
            enhanced_pos = pos.copy()
            enhanced_pos.update({
                'mark_price': mark_price,
                'pnl_pct': pnl_pct,
                'notional_value': notional_value,
                'leverage': leverage,
                'unrealizedProfit': unrealized_pnl
            })
            enhanced_positions.append(enhanced_pos)

        # Use common function to render the table
        render_perpetual_positions_table(
            enhanced_positions,
            title="--- All Perpetual Positions (Raw) ---",
            show_summary=False,  # Don't show summary in dashboard to save space
            indent=""
        )

    def _render_delta_neutral_positions(self):
        """Renders the analyzed delta-neutral positions."""
        render_delta_neutral_positions(
            self.positions,
            self.raw_perp_positions,
            title="Delta-Neutral Positions",
            indent=""
        )

    def _render_other_positions(self):
        """Renders non-delta-neutral positions, like spot-only holdings or imbalanced pairs."""
        render_other_positions(
            self.positions,
            title="Other Holdings (Non-Delta-Neutral)",
            indent=""
        )

    def _render_spot_balances(self):
        """Renders non-stablecoin spot balances including their USD value."""
        render_spot_balances(
            self.spot_balances,
            title="Spot Balances (Excluding Stables)",
            indent=""
        )

    def _render_opportunities(self):
        """Renders potential opportunities for new positions."""
        render_opportunities(
            self.opportunities,
            title="Potential Opportunities",
            indent=""
        )

    def _render_funding_rate_scan(self):
        """Renders the results of an on-demand funding rate scan."""
        if not self.funding_rate_cache:
            return # Don't render if no scan has been run

        # Use common function to render the table
        render_funding_rates_table(
            self.funding_rate_cache,
            title="--- Top Funding Rate APRs (On-Demand Scan) ---",
            show_summary=False,
            indent=""
        )

    def _render_logs(self):
        """Renders the most recent log messages in reverse order (newest first)."""
        print(Fore.GREEN + "\n--- Logs ---" + Style.RESET_ALL)
        
        num_messages = len(self.log_messages)
        max_logs = self.log_messages.maxlen

        # Print the actual log messages in reverse order
        for msg in reversed(self.log_messages):
            print(f"{msg}")

        # Print placeholder lines for the remaining space
        for _ in range(max_logs - num_messages):
            print("[--:--:--]")

    def _render_menu(self):
        """Renders the main menu of available actions."""
        print(Fore.CYAN + "\n--- Menu ---" + Style.RESET_ALL)
        print("[R] Refresh Data   [O] Open Position   [C] Close Position   [F] Scan Funding Rates   [Q] Quit")
        print("[H] Health Check   [B] Balance USDT (Perp/Spot)")



async def check_available_pairs():
    """CLI function to check available pairs that have both spot and perp markets."""
    print(Fore.CYAN + "Checking available delta-neutral pairs..." + Style.RESET_ALL)

    api_manager = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
    )

    try:
        available_pairs = await api_manager.discover_delta_neutral_pairs()

        if not available_pairs:
            print(Fore.YELLOW + "No symbols are currently available in both spot and perpetual markets." + Style.RESET_ALL)
            return

        print(Fore.GREEN + f"\nFound {len(available_pairs)} pairs available for delta-neutral trading:\n" + Style.RESET_ALL)

        # Display in columns for better readability
        pairs_per_row = 4
        for i in range(0, len(available_pairs), pairs_per_row):
            row_pairs = available_pairs[i:i+pairs_per_row]
            formatted_pairs = [f"{pair:<12}" for pair in row_pairs]
            print("  " + "".join(formatted_pairs))

        print(f"\n{Fore.CYAN}Total: {len(available_pairs)} pairs{Style.RESET_ALL}")

    except Exception as e:
        print(Fore.RED + f"ERROR: Failed to check available pairs: {e}" + Style.RESET_ALL)
    finally:
        await api_manager.close()

async def check_current_positions():
    """CLI function to show current delta-neutral positions."""
    print(Fore.CYAN + "Analyzing current positions..." + Style.RESET_ALL)

    api_manager = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
    )

    try:
        print("Fetching comprehensive portfolio data from Aster DEX...")
        portfolio_data = await api_manager.get_comprehensive_portfolio_data()

        if not portfolio_data:
            print(Fore.YELLOW + "No portfolio data available." + Style.RESET_ALL)
            return

        # Extract data from the comprehensive payload
        all_positions = portfolio_data.get('analyzed_positions', [])
        spot_balances = portfolio_data.get('spot_balances', [])
        perp_account_info = portfolio_data.get('perp_account_info', {})
        raw_perp_positions = portfolio_data.get('raw_perp_positions', [])
        delta_neutral_positions = [p for p in all_positions if p.get('is_delta_neutral')]
        other_positions = [p for p in all_positions if not p.get('is_delta_neutral')]

        # Calculate portfolio totals
        spot_usdt_balance = next((float(b.get('free', 0)) for b in spot_balances if b.get('asset') == 'USDT'), 0.0)
        assets = perp_account_info.get('assets', [])
        perp_usdt = next((float(a.get('walletBalance', 0)) for a in assets if a.get('asset') == 'USDT'), 0.0)
        perp_usdc = next((float(a.get('walletBalance', 0)) for a in assets if a.get('asset') == 'USDC'), 0.0)
        perp_usdf = next((float(a.get('walletBalance', 0)) for a in assets if a.get('asset') == 'USDF'), 0.0)

        # Display portfolio summary
        print(Fore.GREEN + f"\n{'='*70}")
        print("PORTFOLIO SUMMARY")
        print(f"{'='*70}" + Style.RESET_ALL)
        render_portfolio_summary(perp_usdt, perp_usdc, perp_usdf, spot_usdt_balance, title="", indent="")

        # Display delta-neutral positions
        render_delta_neutral_positions(all_positions, raw_perp_positions, title=f"DELTA-NEUTRAL POSITIONS ({len(delta_neutral_positions)} found)")

        # Display other positions
        if other_positions:
            render_other_positions(all_positions, title=f"OTHER HOLDINGS ({len(other_positions)} found)")

        # Summary
        print(Fore.GREEN + f"\n{'='*70}")
        print("SUMMARY")
        print(f"{'='*70}" + Style.RESET_ALL)
        print(f"Delta-Neutral Positions: {len(delta_neutral_positions)}")
        print(f"Other Holdings:          {len(other_positions)}")
        print(f"Total Positions:         {len(all_positions)}")

    except Exception as e:
        print(Fore.RED + f"ERROR: Failed to analyze positions: {e}" + Style.RESET_ALL)
    finally:
        await api_manager.close()

async def check_spot_assets():
    """CLI function to show current spot asset balances."""
    print(Fore.CYAN + "Fetching spot asset balances..." + Style.RESET_ALL)

    api_manager = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
    )

    try:
        print("Fetching comprehensive portfolio data from Aster DEX...")
        portfolio_data = await api_manager.get_comprehensive_portfolio_data()

        if not portfolio_data or 'spot_balances' not in portfolio_data:
            print(Fore.YELLOW + "No spot balance data available." + Style.RESET_ALL)
            return

        spot_balances = portfolio_data['spot_balances']
        render_spot_balances(spot_balances, title="Spot Balances (Excluding Stables)")

    except Exception as e:
        print(Fore.RED + f"ERROR: Failed to fetch spot assets: {e}" + Style.RESET_ALL)
    finally:
        await api_manager.close()

async def check_perpetual_positions():
    """CLI function to show current perpetual positions with detailed PnL analysis."""
    print(Fore.CYAN + "Fetching perpetual positions..." + Style.RESET_ALL)

    api_manager = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
    )

    try:
        print("Fetching comprehensive portfolio data from Aster DEX...")
        portfolio_data = await api_manager.get_comprehensive_portfolio_data()

        if not portfolio_data or 'raw_perp_positions' not in portfolio_data:
            print(Fore.YELLOW + "No perpetual position data available." + Style.RESET_ALL)
            return

        active_positions = portfolio_data['raw_perp_positions']
        perp_account_info = portfolio_data['perp_account_info']

        if not active_positions:
            print(Fore.YELLOW + "No active perpetual positions found." + Style.RESET_ALL)
            return

        # Get account balance information
        assets = perp_account_info.get('assets', [])
        total_wallet_balance = sum(float(a.get('walletBalance', 0)) for a in assets)
        total_unrealized_pnl = sum(float(p.get('unrealizedProfit', 0)) for p in active_positions)
        total_margin_balance = total_wallet_balance + total_unrealized_pnl

        # Display results
        print(Fore.GREEN + f"\n{'='*95}")
        print("PERPETUAL POSITIONS")
        print(f"{'='*95}" + Style.RESET_ALL)

        # Account summary
        print(Fore.GREEN + f"\nACCOUNT SUMMARY" + Style.RESET_ALL)
        print(f"Total Wallet Balance:    ${total_wallet_balance:>12,.2f}")
        print(f"Total Unrealized PnL:    ${total_unrealized_pnl:>12,.2f}")
        print(f"Total Margin Balance:    ${total_margin_balance:>12,.2f}")
        print(f"Active Positions:        {len(active_positions):>12}")

        # Use common function to render the positions table
        render_perpetual_positions_table(active_positions, title="\nPOSITION DETAILS", show_summary=True)

    except Exception as e:
        print(Fore.RED + f"ERROR: Failed to fetch perpetual positions: {e}" + Style.RESET_ALL)
    finally:
        await api_manager.close()

def render_funding_rates_table(funding_data, title="Funding Rates (sorted by APR, highest first)", show_summary=True, indent=""):
    """Common function to render funding rates table with effective APR column.

    Args:
        funding_data: List of funding rate dictionaries with 'symbol', 'rate', 'apr' keys
        title: Title to display above the table
        show_summary: Whether to show summary statistics
        indent: String to prepend to each line for indentation
    """
    if not funding_data:
        print(Fore.YELLOW + f"{indent}No funding rate data available." + Style.RESET_ALL)
        return

    print(Fore.GREEN + f"{indent}{title}:\n" + Style.RESET_ALL)

    # Display header
    header = f"{'Symbol':<12} {'Current Rate':>15} {'APR (%)':>20} {'Effective APR (%)':>18}"
    print(f"{indent}{header}")
    print(f"{indent}" + "-" * len(header))

    # Display funding rates
    for item in funding_data:
        rate = item['rate']
        apr = item['apr']
        effective_apr = apr / 2  # Divide by 2 since leverage is 1x for delta-neutral
        apr_color = Fore.GREEN if apr > 0 else Fore.RED
        effective_color = Fore.GREEN if effective_apr > 0 else Fore.RED
        print(f"{indent}{item['symbol']:<12} {rate:>15.6f} {apr_color}{apr:>20.2f}{Style.RESET_ALL} {effective_color}{effective_apr:>18.2f}{Style.RESET_ALL}")

    if show_summary:
        # Summary statistics
        positive_rates = [item for item in funding_data if item['apr'] > 0]
        negative_rates = [item for item in funding_data if item['apr'] < 0]

        print(f"\n{indent}{Fore.CYAN}Summary:")
        print(f"{indent}  Positive APR pairs: {len(positive_rates)}")
        print(f"{indent}  Negative APR pairs: {len(negative_rates)}")
        if positive_rates:
            highest_apr = max(positive_rates, key=lambda x: x['apr'])
            highest_effective_apr = highest_apr['apr'] / 2
            print(f"{indent}  Highest APR: {highest_apr['symbol']} ({highest_apr['apr']:.2f}% -> {highest_effective_apr:.2f}% effective)")
        print(f"{indent}  Total pairs scanned: {len(funding_data)}{Style.RESET_ALL}")


def render_perpetual_positions_table(positions_data, title="POSITION DETAILS", show_summary=True, indent=""):
    """Common function to render perpetual positions table with % gain column.
    Args:
        positions_data: List of position dictionaries with calculated fields
        title: Title to display above the table
        show_summary: Whether to show summary statistics
        indent: String to prepend to each line for indentation
    """
    if not positions_data:
        print(Fore.YELLOW + f"{indent}No active perpetual positions found." + Style.RESET_ALL)
        return

    print(Fore.GREEN + f"{indent}{title}" + Style.RESET_ALL)

    # Header with % gain column
    header = f"{'Symbol':<12} {'Side':<5} {'Size':>12} {'Entry':>12} {'Mark':>12} {'Leverage':>8} {'Notional':>12} {'PnL USD':>12} {'PnL %':>8}"
    print(f"{indent}{header}")
    print(f"{indent}" + "-" * len(header))

    # Sort positions by unrealized PnL (highest first)
    sorted_positions = sorted(positions_data, key=lambda x: float(x.get('unrealizedProfit', 0)), reverse=True)

    total_notional = 0
    total_pnl = 0
    profitable_positions = 0
    losing_positions = 0

    for pos in sorted_positions:
        symbol = pos.get('symbol', 'N/A')
        position_amt = float(pos.get('positionAmt', 0))
        entry_price = float(pos.get('entryPrice', 0))
        mark_price = pos.get('mark_price', entry_price)
        leverage = pos.get('leverage', 1)
        notional_value = pos.get('notional_value', 0)
        unrealized_pnl = float(pos.get('unrealizedProfit', 0))
        pnl_pct = pos.get('pnl_pct', 0)

        total_notional += notional_value
        total_pnl += unrealized_pnl

        if unrealized_pnl > 0:
            profitable_positions += 1
        elif unrealized_pnl < 0:
            losing_positions += 1

        # Determine side and colors
        if position_amt > 0:
            side = "LONG"
            side_color = Fore.GREEN
        else:
            side = "SHORT"
            side_color = Fore.RED

        size = abs(position_amt)

        # Color coding based on PnL for the row
        if unrealized_pnl > 0:
            row_color = Fore.GREEN
        elif unrealized_pnl < 0:
            row_color = Fore.RED
        else:
            row_color = Fore.YELLOW

        # Format the row with colored side text
        print(f"{indent}{symbol:<12} {side_color}{side:<5}{Style.RESET_ALL} {row_color}{size:>12.6f} {entry_price:>12.4f} {mark_price:>12.4f} {leverage:>8.1f}x {notional_value:>12,.2f} {unrealized_pnl:>12.2f} {pnl_pct:>7.2f}%{Style.RESET_ALL}")

    if show_summary:
        # Summary statistics
        print(f"\n{indent}{Fore.CYAN}Portfolio Summary:")
        print(f"{indent}  Total Notional Value: ${total_notional:>12,.2f}")
        print(f"{indent}  Total Unrealized PnL: ${total_pnl:>12,.2f}")
        print(f"{indent}  Profitable Positions: {profitable_positions:>2}")
        print(f"{indent}  Losing Positions:     {losing_positions:>2}")

        if sorted_positions:
            best_position = sorted_positions[0]
            worst_position = sorted_positions[-1]
            print(f"{indent}  Best Performer:  {best_position.get('symbol', 'N/A')} ({best_position.get('pnl_pct', 0):.2f}%)")
            print(f"{indent}  Worst Performer: {worst_position.get('symbol', 'N/A')} ({worst_position.get('pnl_pct', 0):.2f}%)")
        print(f"{indent}  Total Positions: {len(sorted_positions)}{Style.RESET_ALL}")


def render_portfolio_summary(perp_usdt_balance, perp_usdc_balance, perp_usdf_balance, spot_usdt_balance, title="Portfolio Summary", indent=""):
    """Common function to render portfolio summary section.
    Args:
        perp_usdt_balance: Perpetual USDT balance
        perp_usdc_balance: Perpetual USDC balance
        perp_usdf_balance: Perpetual USDF balance
        spot_usdt_balance: Spot USDT balance
        title: Title to display above the summary
        indent: String to prepend to each line for indentation
    """
    print(Fore.GREEN + f"{indent}--- {title} ---" + Style.RESET_ALL)

    perp_margin_balance = perp_usdt_balance + perp_usdc_balance + perp_usdf_balance
    total_portfolio_value = perp_margin_balance + spot_usdt_balance

    print(f"{indent}Perp Margin Balance: ${perp_margin_balance:,.2f} (USDT: {perp_usdt_balance:.2f}, USDC: {perp_usdc_balance:.2f}, USDF: {perp_usdf_balance:.2f})")
    print(f"{indent}Spot USDT Balance:   ${spot_usdt_balance:,.2f}")
    print(f"{indent}Total Portfolio USD: ${total_portfolio_value:,.2f}")


def render_delta_neutral_positions(positions_data, raw_perp_positions, title="Delta-Neutral Positions", indent=""):
    """Common function to render delta-neutral positions table.
    Args:
        positions_data: List of position dictionaries with delta-neutral analysis
        raw_perp_positions: List of raw perpetual position data for PnL lookup
        title: Title to display above the table
        indent: String to prepend to each line for indentation
    """
    print(Fore.GREEN + f"{indent}--- {title} ---" + Style.RESET_ALL)

    dn_positions = [p for p in positions_data if p.get('is_delta_neutral')]

    if not dn_positions:
        print(f"{indent}No delta-neutral positions found.")
        return

    header = f"{'Symbol':<12} {'Spot Balance':>15} {'Perp Position':>15} {'Net Delta':>12} {'Value (spot+perp+pnl)':>25} {'Imbalance':>12} {' Eff. APR (%)':>12}"
    print(f"{indent}{header}")
    print(f"{indent}" + "-" * len(header))

    total_dn_value = 0
    for pos in dn_positions:
        symbol = pos.get('symbol', 'N/A')
        spot_balance = pos.get('spot_balance', 0.0)
        perp_position = pos.get('perp_position', 0.0)
        net_delta = pos.get('net_delta', 0.0)
        imbalance = pos.get('imbalance_pct', 0.0)
        apr = pos.get('current_apr', 'N/A')
        apr_str = f"{apr/2.0:.2f}" if isinstance(apr, (int, float)) else str(apr)

        # Find the corresponding raw perp position to get PnL and price
        raw_pos = next((p for p in raw_perp_positions if p.get('symbol') == symbol), None)
        pnl = float(raw_pos.get('unrealizedProfit', 0)) if raw_pos else 0.0
        current_price = float(raw_pos.get('markPrice', 0)) if raw_pos else pos.get('current_price', 0.0)

        # Calculate different components of value
        spot_value_usd = spot_balance * current_price
        short_value_usd = abs(perp_position) * current_price

        # User-defined formula for "Value"
        final_value = spot_value_usd + short_value_usd + pnl

        total_dn_value += final_value

        # Color coding based on spot value warning levels
        if spot_value_usd < 5:
            row_color = Fore.RED  # Critical - below $5
        elif spot_value_usd < 10:
            row_color = Fore.YELLOW  # Warning - below $10
        else:
            row_color = Fore.GREEN  # Healthy - above $10

        print(row_color + f"{indent}{symbol:<12} {spot_balance:>15.6f} {perp_position:>15.6f} {net_delta:>12.6f} {f'${final_value:,.2f}':>25} {f'{imbalance:.2f}%':>12} {apr_str:>12}" + Style.RESET_ALL)

    print(f"{indent}{Fore.CYAN}Total Delta-Neutral Value: ${total_dn_value:,.2f}{Style.RESET_ALL}")


def render_spot_balances(spot_balances, title="Spot Balances (Excluding Stables)", indent=""):
    """Common function to render spot balances table.
    Args:
        spot_balances: List of spot balance dictionaries
        title: Title to display above the table
        indent: String to prepend to each line for indentation
    """
    print(Fore.GREEN + f"{indent}--- {title} ---" + Style.RESET_ALL)

    non_stable_balances = [b for b in spot_balances if b.get('asset') not in ['USDT', 'USDC', 'USDF'] and float(b.get('free', 0)) + float(b.get('locked', 0)) > 0]

    if not non_stable_balances:
        print(f"{indent}No significant non-stablecoin spot balances found.")
        return

    header = f"{'Asset':<10} {'Free':>15} {'Locked':>15} {'Value (USD)':>18}"
    print(f"{indent}{header}")
    print(f"{indent}" + "-" * (len(header) + 4))

    total_spot_value = 0
    for balance in non_stable_balances:
        asset = balance.get('asset', 'N/A')
        free = float(balance.get('free', 0))
        locked = float(balance.get('locked', 0))
        value_usd = balance.get('value_usd', 0.0)
        total_spot_value += value_usd
        print(f"{indent}{asset:<10} {free:>15.6f} {locked:>15.6f} {value_usd:>18,.2f}")

    print(f"{indent}{Fore.CYAN}Total Non-Stable Value: ${total_spot_value:,.2f}{Style.RESET_ALL}")


def render_other_positions(positions_data, title="Other Holdings (Non-Delta-Neutral)", indent=""):
    """Common function to render non-delta-neutral positions table.
    Args:
        positions_data: List of position dictionaries with delta-neutral analysis
        title: Title to display above the table
        indent: String to prepend to each line for indentation
    """
    other_positions = [p for p in positions_data if not p.get('is_delta_neutral')]

    if not other_positions:
        return  # Don't render if no other positions

    print(Fore.GREEN + f"{indent}--- {title} ---" + Style.RESET_ALL)

    header = f"{'Symbol':<12} {'Spot Balance':>15} {'Perp Position':>15} {'Net Delta':>12} {'Value':>15} {'Imbalance':>12}"
    print(f"{indent}{header}")
    print(f"{indent}" + "-" * len(header))

    for pos in other_positions:
        symbol = pos.get('symbol', 'N/A')
        spot_balance = pos.get('spot_balance', 0.0)
        perp_position = pos.get('perp_position', 0.0)
        net_delta = pos.get('net_delta', 0.0)
        value_usd = pos.get('position_value_usd', 0.0)
        imbalance = pos.get('imbalance_pct', 0.0)

        print(Fore.YELLOW + f"{indent}{symbol:<12} {spot_balance:>15.6f} {perp_position:>15.6f} {net_delta:>12.6f} ${value_usd:>14,.2f} {imbalance:>11.2f}%" + Style.RESET_ALL)


def render_opportunities(opportunities_data, title="Potential Opportunities", indent=""):
    """Common function to render opportunities section.
    Args:
        opportunities_data: List of opportunity strings
        title: Title to display above the opportunities
        indent: String to prepend to each line for indentation
    """
    if not opportunities_data:
        return  # Do not render the section if there are no opportunities

    print(Fore.GREEN + f"{indent}--- {title} ---" + Style.RESET_ALL)
    for opp in opportunities_data:
        print(f"{indent}- {opp}")


async def perform_health_check_analysis(api_manager):
    """
    Shared health check logic that analyzes positions and returns health issues.
    Returns: (health_issues, critical_issues, dn_positions_count, position_pnl_data)
    """
    # Fetch position analysis data
    results = await asyncio.gather(
        api_manager.analyze_current_positions(),
        api_manager.get_perp_account_info(),
        return_exceptions=True
    )

    analysis_results = results[0] if isinstance(results[0], dict) else {}
    perp_account_info = results[1] if isinstance(results[1], dict) else {}

    if not analysis_results:
        return [], [], 0

    # Process positions data into list format
    all_positions = list(analysis_results.values())

    # Use strategy logic for core health analysis
    health_issues, critical_issues, dn_positions_count = DeltaNeutralLogic.perform_portfolio_health_analysis(all_positions)

    # Add additional PnL and price-specific checks for delta-neutral positions
    dn_positions = [p for p in all_positions if p.get('is_delta_neutral')]
    raw_perp_positions = [p for p in perp_account_info.get('positions', []) if float(p.get('positionAmt', 0)) != 0]

    # Fetch current prices for perpetual positions
    if raw_perp_positions:
        price_tasks = [api_manager.get_perp_book_ticker(p['symbol']) for p in raw_perp_positions]
        price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
        for i, pos in enumerate(raw_perp_positions):
            price_data = price_results[i]
            if not isinstance(price_data, Exception) and price_data.get('bidPrice'):
                pos['markPrice'] = (float(price_data['bidPrice']) + float(price_data['askPrice'])) / 2

    # Add PnL and liquidity specific checks and collect position data
    position_pnl_data = []

    for pos in dn_positions:
        symbol = pos.get('symbol', 'N/A')
        spot_balance = pos.get('spot_balance', 0.0)

        # Find corresponding raw perp position to get PnL data and price
        perp_pos = next((p for p in raw_perp_positions if p.get('symbol') == symbol), None)
        current_price = 0.0
        pnl_pct = None
        position_value_usd = pos.get('position_value_usd', 0.0)

        if perp_pos:
            entry_price = float(perp_pos.get('entryPrice', 0))
            mark_price = perp_pos.get('markPrice', entry_price)
            current_price = mark_price
            position_amt = float(perp_pos.get('positionAmt', 0))

            # Calculate PnL percentage for short position
            if entry_price > 0 and position_amt < 0:  # Short position
                pnl_pct = ((entry_price - mark_price) / entry_price) * 100

                # Check for PnL warnings
                if pnl_pct <= -50:
                    critical_issues.append(f"CRITICAL: {symbol} short position PnL: {pnl_pct:.2f}% (below -50%)")
                elif pnl_pct <= -25:
                    health_issues.append(f"WARNING: {symbol} short position PnL: {pnl_pct:.2f}% (below -25%)")

        # Calculate spot position value using current price
        spot_value_usd = spot_balance * current_price

        # Check spot position value for liquidity concerns
        if spot_value_usd < 10:
            if spot_value_usd < 5:
                critical_issues.append(f"CRITICAL: {symbol} spot position value: ${spot_value_usd:.2f} (below $5 - impossible to close)")
            else:
                health_issues.append(f"WARNING: {symbol} spot position value: ${spot_value_usd:.2f} (below $10 - rebalancing advised)")

        # Update position with current price for rendering
        pos['current_price'] = current_price

        # Store position data for display
        position_pnl_data.append({
            'symbol': symbol,
            'position_value_usd': position_value_usd,
            'pnl_pct': pnl_pct,
            'imbalance_pct': pos.get('imbalance_pct', 0.0),
            'spot_value_usd': spot_value_usd
        })

    return health_issues, critical_issues, dn_positions_count, position_pnl_data


async def check_funding_rates():
    """CLI function to check funding rates for all available pairs."""
    print(Fore.CYAN + "Fetching funding rates for delta-neutral pairs..." + Style.RESET_ALL)

    api_manager = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
    )

    try:
        funding_data = await api_manager.get_all_funding_rates()

        if not funding_data:
            print(Fore.YELLOW + "No funding rate data available or no delta-neutral pairs found." + Style.RESET_ALL)
            return

        # Use common function to render the table
        render_funding_rates_table(funding_data)

    except Exception as e:
        print(Fore.RED + f"ERROR: Failed to fetch funding rates: {e}" + Style.RESET_ALL)
    finally:
        await api_manager.close()

async def check_portfolio_health():
    """CLI function to perform portfolio health check."""
    print(Fore.CYAN + "Performing portfolio health check..." + Style.RESET_ALL)

    api_manager = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
    )

    try:
        print("Fetching current position data...")

        # Use shared health check logic
        health_issues, critical_issues, dn_positions_count, position_pnl_data = await perform_health_check_analysis(api_manager)

        if dn_positions_count == 0:
            print(Fore.YELLOW + "No delta-neutral positions found to check." + Style.RESET_ALL)
            return

        # Display results
        print(Fore.GREEN + f"\n{'='*70}")
        print("PORTFOLIO HEALTH CHECK RESULTS")
        print(f"{'='*70}" + Style.RESET_ALL)

        print(Fore.CYAN + "\nHealth Check Criteria:" + Style.RESET_ALL)
        print(f"  {Fore.GREEN}Spot USD > $10{Style.RESET_ALL}: Healthy")
        print(f"  {Fore.YELLOW}Spot USD < $10{Style.RESET_ALL}: Warning (rebalancing advised)")
        print(f"  {Fore.RED}Spot USD < $5{Style.RESET_ALL}: Critical (impossible to close)")
        print(f"  {Fore.YELLOW}Short PnL < -25%{Style.RESET_ALL}: Warning")
        print(f"  {Fore.RED}Short PnL < -50%{Style.RESET_ALL}: Critical")

        if critical_issues:
            print(Fore.RED + "\nCRITICAL ISSUES:" + Style.RESET_ALL)
            for issue in critical_issues:
                print(Fore.RED + f"  {issue}" + Style.RESET_ALL)

        if health_issues:
            print(Fore.YELLOW + "\nWARNINGS:" + Style.RESET_ALL)
            for issue in health_issues:
                print(Fore.YELLOW + f"  {issue}" + Style.RESET_ALL)

        # Display position PnL summary
        if position_pnl_data:
            print(Fore.CYAN + "\nPOSITION PnL SUMMARY:" + Style.RESET_ALL)
            header = f"{'Symbol':<12} {'Value':<12} {'Imbalance':<10} {'PnL %':<10}"
            print(header)
            print("-" * len(header))

            for pos_data in position_pnl_data:
                symbol = pos_data['symbol']
                value_usd = pos_data['position_value_usd']
                spot_value_usd = pos_data['spot_value_usd']
                imbalance_pct = pos_data['imbalance_pct']
                pnl_pct = pos_data['pnl_pct']

                # Color code based on spot value thresholds
                if spot_value_usd < 5:
                    row_color = Fore.RED  # Critical
                elif spot_value_usd < 10:
                    row_color = Fore.YELLOW  # Warning
                else:
                    row_color = Fore.GREEN  # Healthy

                # Color code PnL based on performance
                if pnl_pct is not None:
                    if pnl_pct >= -25:  # Good PnL (above -25%)
                        pnl_color = Fore.GREEN
                        pnl_str = f"{pnl_pct:+.2f}%"
                    else:  # Bad PnL (below -25%, already in warnings/critical)
                        pnl_color = Fore.RED
                        pnl_str = f"{pnl_pct:+.2f}%"
                else:
                    pnl_color = Fore.YELLOW
                    pnl_str = "NA"

                print(f"{row_color}{symbol:<12} ${value_usd:<11.2f} {imbalance_pct:<9.2f}% {pnl_color}{pnl_str:<10}{Style.RESET_ALL}")

        if not critical_issues and not health_issues:
            print(Fore.GREEN + "\nALL CLEAR: No health issues detected with your positions." + Style.RESET_ALL)
        else:
            print(f"\n{Fore.CYAN}RECOMMENDATION:{Style.RESET_ALL}")
            if critical_issues:
                print(f"{Fore.RED}  URGENT: Critical issues detected. Consider immediate rebalancing or position closure.{Style.RESET_ALL}")
            else:
                print(f"{Fore.YELLOW}  Consider rebalancing your positions to address the warnings above.{Style.RESET_ALL}")
            print(f"{Fore.CYAN}  Run: python delta_neutral_bot.py --rebalance{Style.RESET_ALL}")

        # Summary
        print(f"\n{Fore.CYAN}SUMMARY:")
        print(f"  Delta-Neutral Positions Checked: {dn_positions_count}")
        print(f"  Critical Issues Found:           {len(critical_issues)}")
        print(f"  Warnings Found:                  {len(health_issues)}")
        print(f"  Health Status:                   {'CRITICAL' if critical_issues else 'WARNING' if health_issues else 'HEALTHY'}{Style.RESET_ALL}")

    except Exception as e:
        print(Fore.RED + f"ERROR: Failed to perform health check: {e}" + Style.RESET_ALL)
    finally:
        await api_manager.close()

async def rebalance_usdt_cli():
    """CLI function to rebalance USDT between spot and perpetual accounts 50/50."""
    print(Fore.CYAN + "Rebalancing USDT between spot and perpetual accounts..." + Style.RESET_ALL)

    api_manager = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
    )

    try:
        print("Analyzing current USDT distribution...")
        result = await api_manager.rebalance_usdt_50_50()

        # Display current state
        print("\n" + Fore.CYAN + "=== USDT BALANCE ANALYSIS ===" + Style.RESET_ALL)
        print(f"Current Spot USDT:     ${result['current_spot_usdt']:>10.2f}")
        print(f"Current Perp USDT:     ${result['current_perp_usdt']:>10.2f}")
        print(f"Total Available USDT:  ${result['total_usdt']:>10.2f}")
        print(f"Target Each (50/50):   ${result['target_each']:>10.2f}")

        if not result['transfer_needed']:
            print(Fore.GREEN + "\n✓ ALREADY BALANCED: Your USDT is already distributed 50/50 (within $1)" + Style.RESET_ALL)
            return

        # Show transfer details and ask for confirmation
        print(f"\nTransfer Required:")
        print(f"  Amount:     ${result['transfer_amount']:.2f}")
        print(f"  Direction:  {result['transfer_direction'].replace('_', ' → ')}")

        print(Fore.YELLOW + f"\nConfirm transfer of ${result['transfer_amount']:.2f} USDT?" + Style.RESET_ALL)
        confirmation = input("Type 'yes' to proceed: ").strip().lower()

        if confirmation != 'yes':
            print("Transfer cancelled.")
            return

        # Execute the transfer
        print("Executing transfer...")
        transfer_result = await api_manager.transfer_between_spot_and_perp(
            'USDT', result['transfer_amount'], result['transfer_direction']
        )

        if transfer_result and transfer_result.get('status') == 'SUCCESS':
            print(Fore.GREEN + f"[SUCCESS] Transfer completed successfully!" + Style.RESET_ALL)
            print(f"Transaction ID: {transfer_result.get('tranId', 'N/A')}")
        else:
            print(Fore.RED + f"Transfer failed: {transfer_result}" + Style.RESET_ALL)

    except Exception as e:
        print(Fore.RED + f"ERROR: Failed to rebalance USDT: {e}" + Style.RESET_ALL)
    finally:
        await api_manager.close()

async def open_position_cli(symbol: str, capital: float, auto_confirm: bool = False):
    """CLI function to open a new delta-neutral position."""
    print(Fore.CYAN + f"Attempting to open a ${capital:.2f} USD position for {symbol}..." + Style.RESET_ALL)

    api_manager = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
    )

    try:
        # 1. Perform Dry Run to get trade details
        print("Calculating trade details (dry run)...")
        trade_plan = await api_manager.prepare_and_execute_dn_position(symbol, capital, dry_run=True)

        if not trade_plan.get('success'):
            error_message = trade_plan.get('message', 'No error message provided.')
            print(f"{Fore.RED}Error: {error_message}{Style.RESET_ALL}")
            return

        # 2. Show Confirmation
        details = trade_plan['details']
        base_asset = symbol.replace('USDT', '')
        print("\n" + Fore.YELLOW + "--- TRADE PLAN (ADJUSTED FOR LOT SIZE) ---" + Style.RESET_ALL)
        print(f"Symbol: {details['symbol']}, Initial Capital: ${details['capital_to_deploy']:.2f}")
        print(f"Action: Open delta-neutral position via MARKET orders at 1x leverage.")
        print(Fore.MAGENTA + f"Perp Lot Size Filter (stepSize): {details['lot_size_filter'].get('stepSize') if details['lot_size_filter'] else 'N/A'}" + Style.RESET_ALL)
        print(f"Final Perp Qty:   {details['final_perp_qty']:.8f}")
        print("-"*40)
        if (details['existing_spot_quantity'] * details['spot_price']) > 0:
            print(Fore.CYAN + f"Utilizing Existing Spot: {details['existing_spot_quantity']:.8f} {base_asset} (${(details['existing_spot_quantity'] * details['spot_price']):.2f})" + Style.RESET_ALL)
        print(f"Spot BUY Qty: {details['spot_qty_to_buy']:.8f} (${details['spot_capital_to_buy']:.2f})")
        print(f"Perp SELL Qty: {details['final_perp_qty']:.8f} (${details['final_perp_qty'] * details['spot_price']:.2f})")

        # 3. Confirm and Execute
        if not auto_confirm:
            confirm = input("\nPress Enter to confirm (or enter 'x' to cancel): ")
            if confirm.strip().lower() == 'x' or confirm.strip() != '':
                print("Trade execution cancelled by user.")
                return
        
        print("\nExecuting trades...")
        exec_result = await api_manager.prepare_and_execute_dn_position(symbol, capital, dry_run=False)

        if exec_result.get('success'):
            print(f"{Fore.GREEN}{exec_result.get('message')}{Style.RESET_ALL}")
            print(f"Spot Order: {exec_result.get('spot_order')}")
            print(f"Perp Order: {exec_result.get('perp_order')}")
        else:
            print(f"{Fore.RED}Execution failed: {exec_result.get('message')}{Style.RESET_ALL}")

    finally:
        await api_manager.close()

async def run_interactive_open_workflow():
    """Helper to run the interactive open position workflow from the CLI."""
    app = DashboardApp()
    app.is_standalone_workflow = True
    # Manually initialize the app state before running the workflow
    print("Initializing app and fetching market data...")
    await app._fetch_and_update_data()
    app.interactive_mode = True # Ensure it behaves like the interactive command
    await app._open_position_workflow()
    await app.api_manager.close()

async def close_position_cli(symbol: str, auto_confirm: bool = False):
    """CLI function to close a delta-neutral position."""
    print(Fore.CYAN + f"Attempting to close position for {symbol}..." + Style.RESET_ALL)

    api_manager = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
    )

    try:
        if not auto_confirm:
            confirm = input(f"Are you sure you want to close the position for {symbol}? (yes/no): ").strip().lower()
            if confirm != 'yes':
                print("Operation cancelled.")
                return

        print(f"Executing closing trades for {symbol}...")
        close_result = await api_manager.execute_dn_position_close(symbol)

        if close_result.get('success'):
            print(f"{Fore.GREEN}{close_result.get('message')}{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}Failed to close position: {close_result.get('message')}{Style.RESET_ALL}")

    finally:
        await api_manager.close()

async def run_interactive_close_workflow():
    """Helper to run the interactive close position workflow from the CLI."""
    app = DashboardApp()
    # Manually initialize the app state before running the workflow
    print("Initializing app and fetching market data...")
    await app._fetch_and_update_data()
    app.interactive_mode = True # Ensure it behaves like the interactive command
    app.is_standalone_workflow = True # Ensure messages are printed to console
    await app._close_position_workflow()
    await app.api_manager.close()

def main():
    """The main function to run the bot."""
    parser = argparse.ArgumentParser(description="Delta-Neutral Funding Rate Farming Bot")
    parser.add_argument('--test', action='store_true', help="Run in test mode (fetch, render once, then exit)")
    parser.add_argument('--pairs', action='store_true', help="Check available pairs for delta-neutral trading")
    parser.add_argument('--funding-rates', action='store_true', help="Check current funding rates for all available pairs")
    parser.add_argument('--positions', action='store_true', help="Show current delta-neutral positions and portfolio summary")
    parser.add_argument('--spot-assets', action='store_true', help="Show current spot asset balances with USD values")
    parser.add_argument('--perpetual', action='store_true', help="Show current perpetual positions with PnL analysis")
    parser.add_argument('--health-check', action='store_true', help="Perform portfolio health check for position risks")
    parser.add_argument('--rebalance', action='store_true', help="Rebalance USDT between spot and perpetual accounts 50/50")
    parser.add_argument('--open', nargs='*', help="Open a new delta-neutral position. Runs interactively if no symbol/capital is provided.")
    parser.add_argument('--close', nargs='?', const=True, default=None, help="Close a delta-neutral position. Runs interactively if no symbol is provided.")
    parser.add_argument('--yes', action='store_true', help="Bypass confirmation for non-interactive commands like --open.")
    args = parser.parse_args()

    # Check for required environment variables
    required_vars = ['API_USER', 'API_SIGNER', 'API_PRIVATE_KEY', 'APIV1_PUBLIC_KEY', 'APIV1_PRIVATE_KEY']
    if not all(os.getenv(var) for var in required_vars):
        print(Fore.RED + "ERROR: Not all required environment variables are set in your .env file." + Style.RESET_ALL)
        print("Please ensure API_USER, API_SIGNER, API_PRIVATE_KEY, APIV1_PUBLIC_KEY, and APIV1_PRIVATE_KEY are configured.")
        sys.exit(1)

    # Handle CLI-only operations
    if args.pairs:
        try:
            asyncio.run(check_available_pairs())
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
        return

    if args.funding_rates:
        try:
            asyncio.run(check_funding_rates())
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
        return

    if args.positions:
        try:
            asyncio.run(check_current_positions())
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
        return

    if args.spot_assets:
        try:
            asyncio.run(check_spot_assets())
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
        return

    if args.perpetual:
        try:
            asyncio.run(check_perpetual_positions())
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
        return

    if getattr(args, 'health_check', False):
        try:
            asyncio.run(check_portfolio_health())
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
        return

    if args.open is not None:
        if len(args.open) == 0:
            # Interactive mode
            try:
                asyncio.run(run_interactive_open_workflow())
            except KeyboardInterrupt:
                print("\nOperation cancelled.")
            return
        elif len(args.open) == 2:
            # Non-interactive CLI mode
            try:
                symbol, capital = args.open
                asyncio.run(open_position_cli(symbol, float(capital), args.yes))
            except (ValueError, IndexError):
                print(f"{Fore.RED}Error: --open requires SYMBOL and CAPITAL arguments.{Style.RESET_ALL}")
            except KeyboardInterrupt:
                print("\nOperation cancelled.")
            return
        else:
            print(f"{Fore.RED}Error: --open takes either 0 or 2 arguments (symbol and capital).{Style.RESET_ALL}")
            return

    if args.close is not None:
        if args.close is True:
            # Interactive mode
            try:
                asyncio.run(run_interactive_close_workflow())
            except KeyboardInterrupt:
                print("\nOperation cancelled.")
            return
        else:
            # Non-interactive CLI mode
            try:
                asyncio.run(close_position_cli(args.close, args.yes))
            except KeyboardInterrupt:
                print("\nOperation cancelled.")
            return

    if args.rebalance:
        try:
            asyncio.run(rebalance_usdt_cli())
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
        return

    # Default behavior: run the dashboard
    app = DashboardApp(is_test_run=args.test)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\nCaught KeyboardInterrupt, shutting down...")

if __name__ == '__main__':
    main()
