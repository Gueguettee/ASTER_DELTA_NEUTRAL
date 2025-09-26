# Delta-Neutral Funding Rate Farming Bot

This project is a terminal-based Python application for managing a delta-neutral funding rate farming strategy on the Aster DEX. The application's architecture is designed to be modular, testable, and secure, separating concerns into three main components.

## Core Architecture

The application is built on a three-tier architecture, ensuring a clean separation between exchange communication, strategy computation, and user interaction.

### 1. The Exchange Gateway: `aster_api_manager.py`

This module serves as the sole communication layer with the Aster DEX. It provides a high-level, unified API manager that abstracts the complexities of interacting with both the Perpetual and Spot markets.

**Key Responsibilities:**
- **Authentication:** Handles the unique Ethereum-based signature mechanism for the perpetuals API and the key/secret-based authentication for the spot API.
- **Data Fetching:** Provides methods to retrieve account balances, positions, order statuses, market data, and exchange trading rules.
- **Order Execution:** Offers simplified methods to place, manage, and cancel both spot and perpetual orders, automatically handling the required precision formatting for all order parameters.

### 2. The Brain: `strategy_logic.py`

This module contains the pure computational logic for the delta-neutral strategy. It is designed to be completely stateless and independent of the live API, which makes it highly testable and reliable. It takes market and account data as input and produces clear, actionable results.

**Key Responsibilities:**
- **Opportunity Analysis:** Identifies viable trading pairs and analyzes funding rate histories to find profitable opportunities.
- **Position Sizing:** Calculates the precise quantities for spot and perpetual legs required to establish a delta-neutral position, accounting for existing holdings and exchange-specific rules like minimum notional value.
- **Risk Management:** Contains logic to assess the health of existing positions by analyzing imbalance and other metrics.

### 3. The UI & Orchestrator: `delta_neutral_bot.py`

This is the main application that brings the other two modules together. It orchestrates the flow of data, presents a terminal-based dashboard to the user, and handles all user interaction.

**Key Responsibilities:**
- **Integration:** Initializes and manages the `AsterApiManager` and `DeltaNeutralLogic` components.
- **Orchestration:** Runs the main application loop, periodically fetching data, feeding it to the strategy logic, and updating the dashboard.
- **User Interface:** Renders the terminal dashboard, displaying all relevant portfolio information, open positions, and potential opportunities in a clear and organized manner.
- **User Interaction:** Handles all keyboard inputs for refreshing data, scanning for opportunities, and executing the workflows for opening and closing positions.

## Running the Application

There are two ways to run the bot: locally using Python or with Docker for development.

### Method 1: Running Locally

#### 1. Install Dependencies

Ensure you have Python 3 installed. Then, install the required libraries using pip:

```bash
pip install -r requirements.txt
```

#### 2. Configure Environment Variables

Create a `.env` file in the root directory of the project. This file will store your API credentials securely. Add your keys to the `.env` file as follows:

```
API_USER=0x...
API_SIGNER=0x...
API_PRIVATE_KEY=0x...
APIV1_PUBLIC_KEY=...
APIV1_PRIVATE_KEY=...
```

See also `.env.example.`

#### 3. Run the Bot

Once your dependencies are installed and your `.env` file is configured, you can start the application by running the main script:

```bash
python delta_neutral_bot.py
```

#### 1. Prerequisites

Ensure you have Docker and Docker Compose installed on your system.

#### 2. Configure Environment Variables

Create the `.env` file as described in the local setup. The `docker-compose.yml` file is already configured to load this file. See `.env.example`.

#### 3. Build and Run the Docker Container

Open a terminal in the project's root directory and run:

```bash
docker-compose build
```

If you add new dependencies to `requirements.txt`, you will need to rebuild the image before running it again.

To start the bot:

```bash
docker-compose run --rm dn_bot
```

The `--rm` flag is added to automatically remove the container when the bot stops, which is good practice for one-off commands.
