# ASTER DEX Delta-Neutral Funding Rate Farming Bot

This project is a terminal-based Python application for managing a delta-neutral funding rate farming strategy on the Aster DEX. It provides an interactive dashboard and a comprehensive set of command-line tools to help users identify and manage arbitrage opportunities between Aster's perpetual and spot markets.

**Support this project**: Use referral link https://www.asterdex.com/en/referral/164f81 to get a 10% rebate on trading fees.

## Dashboard Preview

<img src="terminal.png" alt="Terminal Screenshot" width="800"/>

## Core Architecture

The application is built on a three-tier architecture, ensuring a clean separation between exchange communication, strategy computation, and user interaction.

1.  **`aster_api_manager.py` (The Exchange Gateway):** A high-level, unified API manager for both Aster Perpetual and Spot markets. It handles authentication, data fetching, and order execution, abstracting away the complexities of the different API versions and signing mechanisms.
2.  **`strategy_logic.py` (The Brain):** Contains the pure, stateless computational logic for the delta-neutral strategy, including opportunity analysis, position sizing, and risk management.
3.  **`delta_neutral_bot.py` (The UI & Orchestrator):** The main application that integrates the other modules. It presents the terminal-based dashboard, handles all user interaction, and provides a full suite of CLI commands for non-interactive use.

## Getting Started

### 1. Install Dependencies

Ensure you have Python 3 installed. Then, install the required libraries using pip:

```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables

You need to create both **API** and **Pro API** credentials on Aster Finance.

![API Management](APIs.png)

Create a `.env` file in the root directory of the project by copying the example file:

```bash
cp .env.example .env
```

Then, edit the `.env` file and add your API credentials:

```
API_USER=0x...
API_SIGNER=0x...
API_PRIVATE_KEY=0x...
APIV1_PUBLIC_KEY=...
APIV1_PRIVATE_KEY=...
```

## Running the Application

### Interactive Dashboard

To launch the main interactive dashboard, run the script without any arguments:

```bash
python delta_neutral_bot.py
```

From the dashboard, you can use keyboard shortcuts to refresh data, open/close positions, scan funding rates, and perform health checks.

### Command-Line Interface (CLI)

The bot also provides a powerful set of non-interactive CLI commands for scripting and quick checks.

**General Commands:**

```bash
# Show all available commands
python delta_neutral_bot.py --help

# Check available delta-neutral trading pairs
python delta_neutral_bot.py --pairs

# Show current funding rates with APR calculations
python delta_neutral_bot.py --funding-rates

# Show a comprehensive summary of all positions and balances
python delta_neutral_bot.py --positions

# Show only spot asset balances with USD values
python delta_neutral_bot.py --spot-assets

# Show only perpetual positions with detailed PnL analysis
python delta_neutral_bot.py --perpetual

# Perform a comprehensive health check on all delta-neutral positions
python delta_neutral_bot.py --health-check

# Rebalance USDT 50/50 between spot and perpetual accounts
python delta_neutral_bot.py --rebalance
```

**Trading Commands:**

The `--open` and `--close` commands can be run in two modes:

1.  **Interactive Mode:** Run the command without arguments to launch a guided workflow.
2.  **Non-Interactive Mode:** Provide arguments directly on the command line. Use the `--yes` flag to bypass the final confirmation prompt, allowing for use in scripts.

```bash
# --- Open a Position ---

# Launch the interactive workflow to select a symbol and enter capital
python delta_neutral_bot.py --open

# Open a $100 position on BTCUSDT (will show a plan and ask for confirmation)
python delta_neutral_bot.py --open BTCUSDT 100

# Open a $100 position on BTCUSDT without a confirmation prompt
python delta_neutral_bot.py --open BTCUSDT 100 --yes


# --- Close a Position ---

# Launch the interactive workflow to select a position to close
python delta_neutral_bot.py --close

# Close the BTCUSDT position (will ask for confirmation)
python delta_neutral_bot.py --close BTCUSDT

# Close the BTCUSDT position without a confirmation prompt
python delta_neutral_bot.py --close BTCUSDT --yes
```

## Testing

The project has a comprehensive test suite covering the API manager, strategy logic, and CLI functionality.

```bash
# Run all unit tests
python -m unittest discover -v

# Run integration tests (requires real API credentials in .env)
python run_integration_tests.py
```

## Docker Support

For development, you can also build and run the bot using Docker Compose.

```bash
# Build the image
docker-compose build

# Run the bot (e.g., launch the dashboard)
docker-compose run --rm dn_bot

# Run a CLI command (e.g., check positions)
docker-compose run --rm dn_bot --positions
```