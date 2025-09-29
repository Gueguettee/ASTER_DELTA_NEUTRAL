# GEMINI.md

## Project Overview

This project is a terminal-based Python application for managing a delta-neutral funding rate farming strategy on the Aster DEX. The application is designed to help users identify and manage arbitrage opportunities between Aster's perpetual and spot markets.

The project follows a modular three-tier architecture:

1.  **`aster_api_manager.py` (The Exchange Gateway):** A high-level, unified API manager for both Aster Perpetual and Spot markets. It uses the lower-level `ASTER_codes/api_client.py` for Ethereum-based authentication, data fetching, and order execution.
2.  **`strategy_logic.py` (The Brain):** Contains the pure computational logic for the delta-neutral strategy, including opportunity analysis, position sizing, and risk management. This module is stateless and independent of the live API.
3.  **`delta_neutral_bot.py` (The UI & Orchestrator):** The main application that integrates the other modules, presents a terminal-based dashboard, and handles user interaction. This part is planned but not yet implemented.

The core philosophy of the project emphasizes security (never handling raw private keys for fund transfers), user control (requiring explicit confirmation for all trades), and a robust, modular architecture.

## Building and Running

### Environment Setup

1.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    The main dependencies are `aiohttp` for asynchronous requests and `web3`/`eth-account` for handling the Ethereum-based authentication required by the Aster API.

2.  **Configure environment variables:**
    Create a `.env` file in the project root and add your API credentials:
    ```
    API_USER=0x...
    API_SIGNER=0x...
    API_PRIVATE_KEY=0x...
    APIV1_PUBLIC_KEY=...
    APIV1_PRIVATE_KEY=...
    ```

### Running the Application

The main application is now implemented in `delta_neutral_bot.py`. It can be run as an interactive dashboard or with one-off CLI commands for specific actions.

```bash
# Run the interactive dashboard (default)
python delta_neutral_bot.py

# Or use CLI commands for specific tasks (e.g., check positions)
python delta_neutral_bot.py --positions

# Open a position interactively
python delta_neutral_bot.py --open

# Open a $100 position in BTCUSDT non-interactively
python delta_neutral_bot.py --open BTCUSDT 100 --yes

# Close a position interactively
python delta_neutral_bot.py --close

# Close a BTCUSDT position non-interactively
python delta_neutral_bot.py --close BTCUSDT --yes
```

### Testing

The project has a comprehensive test suite covering the API manager, strategy logic, and CLI functionality.

*   **Run all unit tests:**
    ```bash
    python -m unittest discover -v
    ```

*   **Run integration tests (requires real API credentials):**
    ```bash
    python run_integration_tests.py
    ```

## Development Conventions

*   **Code Style:** The project follows PEP 8 standards and uses type hints extensively.
*   **Authentication:** The perpetuals API uses a custom Ethereum-based signature mechanism, implemented in `ASTER_codes/api_client.py`.
*   **Modularity:** The code is strictly divided into three main components for isolated development and testing.
*   **Stateless Logic:** The core strategy logic is implemented as pure, stateless functions for testability.
*   **Asynchronous:** The project uses `asyncio` and `aiohttp` for non-blocking network I/O.
*   **User Confirmation:** All actions that execute trades or modify the state of the user's account require explicit user confirmation.
*   **No Emojis:** The project avoids using Unicode emojis in code or output to ensure compatibility with Windows terminals.
