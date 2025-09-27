### **Project Goal & Philosophy**

The primary objective is to develop a high-quality, terminal-based Python application for managing a delta-neutral funding rate farming strategy on the Aster DEX.

**Core Philosophy:**
*   **Security First:** The application will never handle raw private keys for on-chain transactions like fund transfers. It will explicitly check balances and instruct the user to perform transfers manually via their wallet.
*   **User Control:** This is a semi-automated tool, not a fully autonomous bot. Every trade execution (opening or closing a position) must be explicitly confirmed by the user.
*   **Robustness through Modularity:** The architecture is strictly divided into three files, each with a distinct responsibility. This allows for isolated development and rigorous unit testing of each component before integration.
*   **Clarity and Maintainability:** The code must be well-documented, follow PEP 8 standards, and use type hints extensively.

AVOID USING EMOJIS !
Use ASTER for all tests (e.g. position open close) in both perp and spot.

## PROJECT STATUS SUMMARY

**COMPLETED STEPS:**
- [DONE] **Step 1**: `aster_api_manager.py` - Exchange Gateway (enhanced with transfers, discovery, precision handling)
- [DONE] **Step 2**: `strategy_logic.py` - Strategic Brain (enhanced with health analysis)
- [DONE] **Step 3**: `delta_neutral_bot.py` - UI & Orchestrator (fully implemented with dashboard and CLI)

**CURRENT STATUS:**
- **Project Complete**: All three core modules are implemented and integrated.
- **Total Methods**: 30+ implemented across all modules.
- **Test Coverage**: 30+ test methods across 3 test files, including CLI and integration tests.
- **Key Features**:
    - Interactive terminal dashboard with real-time updates.
    - Comprehensive CLI for non-interactive operations (`--pairs`, `--positions`, `--health-check`, `--rebalance`, etc.).
    - Automated 50/50 USDT rebalancing between spot and perpetual accounts.
    - Portfolio-wide health checks for imbalance and liquidation risk.
    - Dynamic discovery of tradable delta-neutral pairs.
    - Automated order precision handling for all trades.
    - Full integration of API, strategy, and UI layers.

---

### **Step 1: The Foundation - `aster_api_manager.py` (The Exchange Gateway)**

**Objective:** To create a single, unified, and asynchronous class that abstracts all API communications with both Aster's Perpetual and Spot markets. This module will be the sole point of contact with the exchange.

**File:** `aster_api_manager.py`

**Detailed Implementation Plan:**

1.  **File Setup & Imports:**
    *   Import necessary libraries: `asyncio`, `aiohttp`, `os`, `time`, `hmac`, `hashlib`, `json`, `typing`, `urllib.parse`.
    *   Import the `ApiClient` class from the provided `api_client.py`.
    *   Define constants for the base URLs:
        ```python
        FUTURES_BASE_URL = "https://fapi.asterdex.com"
        SPOT_BASE_URL = "https://sapi.asterdex.com"
        ```

2.  **`AsterApiManager` Class Definition:**
    *   **`__init__(self, api_user, api_signer, api_private_key, apiv1_public, apiv1_private)`:**
        *   Store all API credentials passed as arguments into instance variables.
        *   Instantiate the perpetuals client: `self.perp_client = ApiClient(api_user, api_signer, api_private_key)`.
        *   Instantiate a shared `aiohttp.ClientSession`: `self.session = aiohttp.ClientSession()`. This is more efficient than creating a new one for each request.
    *   **`async def close(self)`:** A crucial cleanup method to be called at the end of the application's lifecycle to close the `aiohttp.ClientSession`.

3.  **Private Helper Methods for Authentication:**
    *   **`_create_spot_signature(self, params: dict) -> str`:**
        *   **Purpose:** To handle the specific HMAC-SHA256 signing required by the Spot API, which differs from the perpetuals client.
        *   **Logic:**
            1.  Take a dictionary of parameters.
            2.  URL-encode the parameters into a query string (`urllib.parse.urlencode`).
            3.  Use `hmac.new` with `self.apiv1_private` as the key and the query string as the message, using `hashlib.sha256`.
            4.  Return the hexdigest of the signature.
    *   **`async def _make_spot_request(self, method: str, path: str, params: dict, signed: bool = False) -> dict`:**
        *   **Purpose:** A generic internal method for making requests to the Spot API.
        *   **Logic:**
            1.  Construct the full URL: `f"{SPOT_BASE_URL}{path}"`.
            2.  Prepare headers: `{'X-MBX-APIKEY': self.apiv1_public}`.
            3.  If `signed` is `True`:
                *   Add `timestamp` and `recvWindow` to `params`.
                *   Generate the signature using `_create_spot_signature(params)`.
                *   Add the `signature` to `params`.
            4.  Make the request using `self.session.request(...)`.
            5.  Raise for status and return the JSON response.

4.  **Public Data Fetching Methods (Read-Only):**
    *   `async def get_perp_account_info(self) -> dict`: Uses `self.perp_client.signed_request` to call `GET /fapi/v3/account`. Returns the full JSON response.
    *   `async def get_spot_account_balances(self) -> list`: Uses `self._make_spot_request` to call `GET /api/v1/account` (signed). Returns the list from the `balances` key.
    *   `async def get_funding_rate_history(self, symbol: str, limit: int = 50) -> list`: Makes a public GET request to `/fapi/v1/fundingRate`.
    *   `async def get_perp_book_ticker(self, symbol: str) -> dict`: Public GET to `/fapi/v1/ticker/bookTicker`.
    *   `async def get_spot_book_ticker(self, symbol: str) -> dict`: Public GET to `/api/v1/ticker/bookTicker` using the spot base URL.
    *   `async def get_perp_order_status(self, symbol: str, order_id: int) -> dict`: Uses `self.perp_client.get_order_status`.
    *   `async def get_spot_order_status(self, symbol: str, order_id: int) -> dict`: Uses `_make_spot_request` to call `GET /api/v1/order` (signed).
    *   `async def get_perp_leverage(self, symbol: str) -> int`: Gets current leverage setting for a perpetual symbol from position risk data.
    *   `async def set_perp_leverage(self, symbol: str, leverage: int = 1) -> dict`: Sets leverage for perpetual symbol (defaults to 1 for delta-neutral strategy).
    *   `async def analyze_current_positions(self) -> Dict[str, Dict[str, Any]]`: Analyzes current positions to detect delta-neutral setups with 2% imbalance threshold.

5.  **Public Execution Methods (Write Actions):**
    *   `async def place_perp_order(...)`: Uses `self.perp_client.place_order`, ensuring `timeInForce='GTX'` for post-only.
    *   `async def place_spot_buy_market_order(self, symbol: str, quote_quantity: str) -> dict`: Uses `_make_spot_request` for `POST /api/v1/order` with `side='BUY'`, `type='MARKET'`, and `quoteOrderQty`.
    *   `async def place_spot_sell_market_order(self, symbol: str, base_quantity: str) -> dict`: Uses `_make_spot_request` for `POST /api/v1/order` with `side='SELL'`, `type='MARKET'`, and `quantity`.
    *   `async def close_perp_position(self, symbol: str, quantity: str, side_to_close: str) -> dict`: Places a `MARKET` order using `self.perp_client` with `reduceOnly=True`. The `side_to_close` is the opposite of the position side.

---
#### **In-Depth Testing Plan for Step 1**

**Test File:** `test_api_manager.py`
**Methodology:** Use the `unittest.IsolatedAsyncioTestCase` framework. Load credentials from a `.env` file to avoid hardcoding.

*   **Test 1: Initialization and Cleanup**
    *   **Name:** `test_initialization_and_close`
    *   **Action:** Create an instance of `AsterApiManager`. Assert that `self.perp_client` and `self.session` are not `None`. Call `await manager.close()` and assert `manager.session.closed` is `True`.
    *   **Goal:** Verify the constructor works and resources are cleaned up properly.

*   **Test 2: Spot Authentication Signature**
    *   **Name:** `test_spot_signature_generation`
    *   **Action:** Create a known set of parameters and a dummy secret key. Call the private `_create_spot_signature` method. Compare the output to a pre-calculated, known-good signature.
    *   **Goal:** Ensure the spot signing mechanism is implemented correctly, as it's a critical and distinct piece of logic.

*   **Test 3: Data Fetching Methods**
    *   **Name:** `test_get_perp_account_info_structure`, `test_get_spot_account_balances_structure`, etc.
    *   **Action:** For each data-fetching method:
        1.  Call the method (e.g., `await manager.get_perp_account_info()`).
        2.  Assert the response is not `None`.
        3.  Assert the top-level data type is correct (e.g., `assertIsInstance(response, dict)`).
        4.  Assert the presence of key fields (e.g., for perp info, assert `'assets'` and `'positions'` are in the response keys).
        5.  For list responses, assert the list is not empty (assuming a funded test account) and that the first element has the expected structure (e.g., a spot balance entry must contain `'asset'`, `'free'`, `'locked'`).
    *   **Goal:** Verify that all read-only endpoints are being called correctly and that the data is parsed into the expected Python types.

*   **Test 4: Edge Case - Invalid Symbol**
    *   **Name:** `test_fetch_methods_with_invalid_symbol`
    *   **Action:** Call methods like `get_perp_book_ticker` with a nonsensical symbol like `"NOTASYMBOL"`.
    *   **Goal:** Verify that the method handles the API error gracefully (e.g., by returning `None` or raising a specific, documented exception) instead of crashing.

*   **Test 5: Controlled Execution Workflow (Requires Manual Confirmation)**
    *   **Name:** `test_full_order_lifecycle`
    *   **Action:**
        1.  Print a large, bold warning: `**WARNING: This test will execute REAL trades. Use a dedicated test account with minimal funds.***`
        2.  Prompt the user: `input("Press Enter to proceed with live execution test...")`.
        3.  Place a small, far-out-of-the-money limit order using `place_perp_order`.
        4.  Assert the response contains an `orderId`.
        5.  `await asyncio.sleep(2)`.
        6.  Call `get_perp_order_status` with the new `orderId`. Assert its status is `'NEW'`.
        7.  Call `close_perp_position` (or a dedicated `cancel_perp_order` method) to cancel it.
        8.  `await asyncio.sleep(2)`.
        9.  Call `get_perp_order_status` again. Assert its status is now `'CANCELED'`.
    *   **Goal:** To perform a safe, end-to-end integration test of the write-actions, confirming that placing, querying, and canceling orders works as a complete sequence.

---

#### **Implementation Notes & Important Discoveries**

**Critical Implementation Details Discovered:**

1. **Session Management - LAZY INITIALIZATION REQUIRED:**
   - **Issue:** Creating `aiohttp.ClientSession()` in `__init__` causes `RuntimeError: no running event loop`
   - **Solution:** Implement lazy session creation in `_make_spot_request` and individual methods
   - **Code Pattern:** Check `if not self.session: self.session = aiohttp.ClientSession()` before use

2. **Order Precision Requirements - ESSENTIAL FOR REAL TRADING:**
   - **Discovery:** Aster DEX enforces strict step size requirements for all orders
   - **ASTER Symbol:** Step size = 0.01, Price tick = 0.00001, Min notional = $5.00
   - **XRP Symbol:** Step size = 0.1, Price tick = 0.0001, Min notional = $5.00
   - **Critical Function Needed:**
     ```python
     def round_to_step(quantity: float, step_size: float) -> float:
         return math.floor(quantity / step_size) * step_size
     ```

3. **Minimum Notional Validation:**
   - **Requirement:** All spot orders must meet minimum $5.00 USD value
   - **Impact:** Orders below this threshold will fail with 400 errors
   - **Validation:** Always verify `quantity * price >= 5.0` before placing orders

4. **Additional Methods Required:**
   - **`cancel_perp_order` method:** Added as wrapper around `perp_client.cancel_order`
   - **`get_perp_leverage` method:** Gets current leverage setting from position risk data
   - **`set_perp_leverage` method:** Sets leverage (default 1x for delta-neutral strategy)
   - **Justification:** Provides consistent API interface, proper session management, and leverage control for risk management

5. **Unicode Character Handling:**
   - **Issue:** Windows console cannot display Unicode characters (checkmarks, X marks, etc.)
   - **Solution:** Use ASCII alternatives like `[OK]`, `[ERROR]`, `[SUCCESS]`

**Testing Enhancements Made:**
- Comprehensive integration test runner (`run_integration_tests.py`)
- Real order execution tests with proper cleanup
- Exchange info validation for precision requirements
- Environment variable loading with python-dotenv

**Files Created Beyond Original Plan:**
- `run_integration_tests.py` - Integration test runner with .env support
- `step1_verification.py` - Comprehensive requirement verification
- `test_comprehensive_methods.py` - Real API method testing
- `cleanup_aster.py` - Position cleanup utilities

**Leverage Management Enhancement:**
- **Security Enhancement**: All methods now enforce 1x leverage for delta-neutral safety
- **API Methods Added**: `get_perp_leverage()` and `set_perp_leverage()` for complete leverage control
- **Strategy Integration**: All core strategy methods enhanced with leverage validation
- **Risk Management**: Leverage-aware liquidation risk calculations and safety warnings
- **Test Coverage**: Comprehensive leverage scenarios in both API and strategy test suites

**Position Analysis Enhancement:**
- **Real-time Detection**: `analyze_current_positions()` method for live delta-neutral analysis
- **Cross-market Analysis**: Compares spot balances with perpetual positions across all symbols
- **Strict Threshold**: 2% imbalance tolerance for accurate delta-neutral identification
- **Comprehensive Metrics**: Net delta, imbalance percentage, position values, leverage status
- **Asset Mapping**: Intelligent base asset extraction (BTC from BTCUSDT, ETH from ETHUSDT)
- **Verified Accuracy**: Successfully tested on real Aster account with live positions
- **Session Management**: Enhanced cleanup for all async operations and API connections

---

### **Step 2: The Core Logic - `strategy_logic.py` (The Brain)**

**Objective:** To create a completely stateless module containing all the pure computational logic for the strategy. This ensures it is highly testable and independent of the live API.

**File:** `strategy_logic.py`

**Detailed Implementation Plan:**

1.  **File Setup & Imports:**
    *   Import `typing` and `statistics`.
    *   Define strategy constants at the top of the file for easy tuning (e.g., `ANNUALIZED_APR_THRESHOLD = 15.0`).

2.  **`DeltaNeutralLogic` Class Definition:**
    *   This will be a container for `@staticmethod`s only. No `__init__`.

3.  **Static Method Implementations:**
    *   `@staticmethod def analyze_funding_opportunities(...)`: As described previously.
    *   `@staticmethod def calculate_position_size(...)`: As described previously.
    *   `@staticmethod def check_position_health(...)`: As described previously.
    *   `@staticmethod def determine_rebalance_action(...)`: As described previously.

---
#### **In-Depth Testing Plan for Step 2**

**Test File:** `test_strategy_logic.py`
**Methodology:** Use `unittest.TestCase` to create a suite of unit tests. All data will be mocked.

*   **Test 1: `analyze_funding_opportunities`**
    *   **Name:** `test_opportunity_analyzer`
    *   **Setup:** Create several mock `funding_histories` dictionaries.
        *   `mock_good_opportunity`: High positive mean, low stdev.
        *   `mock_negative_funding`: Negative mean.
        *   `mock_unstable_funding`: High positive mean, very high stdev.
        *   `mock_low_yield`: Low positive mean, low stdev.
        *   `mock_insufficient_data`: Fewer than 10 data points.
    *   **Action & Assertions:**
        *   Call the function with `mock_good_opportunity`. Assert the returned list contains one item and its `annualized_apr` is correctly calculated.
        *   Call the function with the other mocks. Assert the returned list is empty for each case.
    *   **Goal:** Verify the filtering and calculation logic correctly identifies valid opportunities based on all criteria.

*   **Test 2: `calculate_position_size`**
    *   **Name:** `test_position_sizing`
    *   **Action:** Call the function with sample inputs (e.g., `total_usd_capital=1000.0`, `spot_price=50.0`).
    *   **Assertions:** Assert the returned dictionary contains `spot_quantity` and `perp_quantity`, and that their values are equal to `20.0`. Use `assertAlmostEqual` for float comparisons.
    *   **Goal:** Verify the basic sizing calculation is correct.

*   **Test 3: `check_position_health`**
    *   **Name:** `test_health_checks`
    *   **Setup:** Create mock `perp_position` dictionaries for different scenarios.
        *   `mock_healthy_pos`: `positionAmt=-10`, `spot_balance_qty=10`, `liquidationPrice=1000`, `mark_price=2000`.
        *   `mock_imbalanced_pos`: `positionAmt=-10`, `spot_balance_qty=11`.
        *   `mock_risky_pos`: `positionAmt=-10`, `spot_balance_qty=10`, `liquidationPrice=1980`, `mark_price=2000`.
    *   **Action & Assertions:**
        *   Call with `mock_healthy_pos`. Assert `net_delta` is 0, `liquidation_risk_level` is 'LOW'.
        *   Call with `mock_imbalanced_pos`. Assert `net_delta` is 1 and `imbalance_percentage` is `10.0`.
        *   Call with `mock_risky_pos`. Assert `liquidation_risk_pct` is `1.0` and `liquidation_risk_level` is 'HIGH'.
    *   **Goal:** Ensure all health metrics are calculated correctly.

*   **Test 4: `determine_rebalance_action`**
    *   **Name:** `test_action_determination`
    *   **Action:** Create mock health reports (the output from the previous test function).
    *   **Assertions:**
        *   Feed a report with `liquidation_risk_level='HIGH'`. Assert the return is `'ACTION_CLOSE_POSITION'`.
        *   Feed a report with `imbalance_percentage=10.0`. Assert the return is `'ACTION_REBALANCE'`.
        *   Feed a healthy report. Assert the return is `'ACTION_HOLD'`.
    *   **Goal:** Confirm that the decision-making logic correctly translates a health report into a simple, actionable command.

---

#### **Step 2 Implementation Notes & Completion Status**

**STEP 2 COMPLETED SUCCESSFULLY [OK]**

**Implementation Enhancements Beyond Original Plan:**

1. **Additional Strategic Methods:**
   - **`calculate_rebalance_quantities`:** Advanced rebalancing logic with specific action recommendations
   - **`validate_strategy_preconditions`:** Balance validation before opening positions
   - **Enhanced Error Handling:** Comprehensive edge case coverage

2. **Strategy Constants Optimization:**
   - Tuned for realistic DeFi conditions
   - `ANNUALIZED_APR_THRESHOLD = 15.0%` - Conservative threshold for opportunity identification
   - `MAX_VOLATILITY_THRESHOLD = 0.05` - Stability requirement (5% coefficient of variation)
   - `HIGH_RISK_LIQUIDATION_PCT = 2.0%` - Conservative liquidation risk threshold

3. **Comprehensive Testing Achievements:**
   - **10 Test Methods:** Complete coverage of all static methods, edge cases, and leverage scenarios
   - **100% Test Pass Rate:** All unit tests passing with proper mocking
   - **Boundary Testing:** Zero values, empty inputs, extreme scenarios
   - **Validation Testing:** Strategy constants within reasonable DeFi ranges
   - **Leverage Testing:** Invalid leverage detection, risk escalation, capital efficiency

4. **Code Quality Measures:**
   - **Pure Functions:** All methods are stateless and deterministic
   - **Type Hints:** Complete typing.Dict, typing.List annotations
   - **Documentation:** Comprehensive docstrings for all methods
   - **Modular Design:** Perfect separation from API concerns

**Files Created:**
- `strategy_logic.py` - Core computational logic (237 lines)
- `test_strategy_logic.py` - Complete test suite (234 lines)
- `step2_verification.py` - Comprehensive requirement validation

**Verification Results:**
- [OK] File Setup & Imports
- [OK] Class Definition (Static methods container)
- [OK] All Required Static Methods Present
- [OK] Method Signatures Match Specification
- [OK] Complete Test Coverage
- [OK] All Unit Tests Pass
- [OK] Core Functionality Validated

**Ready for Step 3:** Terminal UI & orchestration integration

**Current Implementation Status:**
- **Files Created:** 4 core files + 6 example/demo files
- **Methods Implemented:** 10 static methods (4 required + 6 enhanced) with leverage integration
- **Test Coverage:** 10 comprehensive tests (4 required + 6 additional) including leverage scenarios
- **Verification:** 100% mega plan compliance confirmed
- **Leverage Integration:** Complete 1x validation and risk management
- **Pair Discovery:** Dynamic + static fallback system implemented
- **Integration Ready:** All methods tested and API manager enhanced

**Key Achievements Beyond Requirements:**
- Advanced pair discovery system for future market expansion
- Enhanced rebalancing logic with specific action recommendations
- Comprehensive risk validation and precondition checking
- Liquidity filtering for position sizing optimization
- Complete edge case and boundary condition coverage
- **Leverage Management System:** Full control and validation for delta-neutral safety

---

### **Step 3: The UI & Orchestrator - `delta_neutral_bot.py`**

**Objective:** To create the main application that integrates the other modules, presents a clear and dynamically updating terminal dashboard, and handles user interaction.

**File:** `delta_neutral_bot.py`

**Detailed Implementation Plan:**

**INTEGRATION NOTES FOR STEP 3:**

Before implementing Step 3, note the enhanced capabilities now available:

1. **Enhanced API Manager** (`aster_api_manager.py`):
   - Added `discover_delta_neutral_pairs()` for dynamic pair discovery
   - Added `get_market_volumes_24h()` for liquidity analysis
   - All original methods plus pair discovery capabilities

2. **Comprehensive Strategy Logic** (`strategy_logic.py`):
   - All 4 required methods plus 6 additional advanced methods
   - Built-in pair discovery: `find_delta_neutral_pairs()`, `filter_viable_pairs()`
   - Enhanced validation: `validate_strategy_preconditions()`
   - Advanced rebalancing: `calculate_rebalance_quantities()`

3. **Available Integration Points:**
   - Automatic pair discovery and filtering
   - Real-time opportunity analysis with 15%+ APR threshold
   - Comprehensive position health monitoring
   - Intelligent rebalancing recommendations
   - Risk validation before position opening

**File Setup & Imports:** Import `asyncio`, `os`, `sys`, `argparse`, `datetime`, and the two custom classes `AsterApiManager` and `DeltaNeutralLogic`.

2.  **`DashboardApp` Class Definition:**
    *   **`__init__`**: Initialize `api_manager`, `logic`, and state variables as previously described.
    *   **`run()`**: The main entry point. It will:
        1.  Create the asyncio tasks for `_main_loop` and `_handle_user_input`.
        2.  Use `asyncio.gather` to run them concurrently.
        3.  Ensure `self.api_manager.close()` is called in a `finally` block to guarantee cleanup.

3.  **Core Tasks & Rendering:**
    *   **`_main_loop()`**: As described previously. This is the heart of the background data refresh.
    *   **`_handle_user_input()`**: As described previously.
    *   **`_render_dashboard()`**: As described previously, with extreme attention to formatting and color-coding for readability.

4.  **User Workflows:**
    *   **`_open_position_workflow()`**: As described previously. Ensure every step is logged to `self.log_messages` for user feedback.
    *   **`_close_position_workflow()`**: As described previously.
    *   **`_add_log(self, message: str)`**: A helper method to add a timestamped message to `self.log_messages` and keep the list trimmed to a fixed size (e.g., 10 messages).

5.  **Main Execution Block:** Parses command-line arguments (if any) and runs the `DashboardApp`.

---
#### **In-Depth Testing Plan for Step 3**

**Methodology:** This phase consists of manual integration and User Acceptance Testing (UAT). The goal is to verify that all components work together seamlessly from the user's perspective.

*   **Test 1: First Launch and Data Population**
    *   **Action:** Run `python delta_neutral_bot.py`.
    *   **Expected Outcome:**
        1.  The dashboard appears without crashing.
        2.  A "Fetching data..." message appears in the logs.
        3.  Within the refresh interval, the "Portfolio", "Positions", and "Spot Balances" sections populate with correct data from your test account.
        4.  The "Opportunities" section populates with a list of symbols or shows "None found".
    *   **Goal:** Verify the initial data loading and rendering pipeline is functional.

*   **Test 2: Dynamic Dashboard Updates**
    *   **Action:** While the bot is running, manually execute a small trade on the Aster DEX website (e.g., buy 1 ASTER on spot).
    *   **Expected Outcome:** On the next refresh cycle (within 30 seconds), the spot balance for ASTER on the dashboard should update to reflect the new amount.
    *   **Goal:** Confirm the background refresh loop is working and correctly updating the application's state and UI.

*   **Test 3: "Open Position" Workflow - Success Path**
    *   **Action:**
        1.  Ensure you have sufficient funds in both spot and perp wallets for a very small position.
        2.  Initiate the "Open Position" workflow from the menu.
        3.  Select a valid opportunity.
        4.  Enter a small capital amount (e.g., 5 USD).
        5.  Review the confirmation screen. Verify the calculated quantities are correct.
        6.  Confirm with 'y'.
    *   **Expected Outcome:**
        1.  The log should show "Placing spot order..." and "Placing perp order...".
        2.  The log should confirm both orders were successful.
        3.  On the next refresh, the new position should appear in the "Current Positions" table with the correct details.
    *   **Goal:** Test the end-to-end "happy path" for opening a position.

*   **Test 4: "Open Position" Workflow - Insufficient Funds Path**
    *   **Action:** Intentionally move funds so that either the spot or perpetual wallet has insufficient balance. Attempt the "Open Position" workflow.
    *   **Expected Outcome:** The application should display a clear error message in the logs, like *"Insufficient USDT in Spot wallet. Required: 5.0, Available: 2.0. Please transfer manually."* It should **not** attempt to place any orders.
    *   **Goal:** Verify the critical security pre-check for balances is working correctly.

*   **Test 5: Position Health Monitoring**
    *   **Action:** With a position open, observe the dashboard.
    *   **Expected Outcome:**
        1.  The "Net Delta" should be close to 0 and green.
        2.  The "Liquidation Risk" should be 'LOW' and green.
    *   **Action 2:** Manually sell a small portion of the spot asset on the exchange to create an imbalance.
    *   **Expected Outcome 2:** On the next refresh, the "Net Delta" should become non-zero and turn yellow/red. The log might suggest a rebalance is needed.
    *   **Goal:** Verify that the bot is correctly integrating the strategy logic to monitor and report the health of live positions.

*   **Test 6: Graceful Shutdown**
    *   **Action:** Press 'q' or Ctrl+C.
    *   **Expected Outcome:** The application should exit cleanly without a traceback. All asyncio tasks should be canceled.
    *   **Goal:** Ensure the application shuts down properly.