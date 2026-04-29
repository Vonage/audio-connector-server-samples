# Vonage Audio connector server sample app

The `sample_app.py` is a demonstration application that showcases the integration of real-time audio communication using the Vonage unified Video API. It establishes a WebSocket connection to facilitate bidirectional audio streaming, allowing users to send and receive audio in real-time. This sample app serves as a foundational example for developers looking to implement audio features in their own applications, leveraging the capabilities of the Vonage platform.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configure Websocket server URLs](#configure-websocket-server-urls)
- [Running the Application](#running-the-application)
- [Usage](#usage)

## Features

- **WebSocket server**
- **Real-time Stream audio** to/from Vonage

## Requirements

- Python **3.10+**
- **Vonage account** Need credentials (application-ID, private-key) from vonage account.
- OR **Opentok account** Need credentials (api-key, api-secret) from opentok account.
- **ngrok** (or any HTTPS tunnel) for local testing

## Installation

1. **Install dependencies**:
    ```sh
    pip install -r unified_sample_app/requirements.txt
    ```

2. **Create .env**:
    Copy the example environment file and update with your settings. This will
    export these environment variables in your local terminal with sample app
    ```sh
    cp unified_sample_app/env.example .env
    ```

3. **Set the opentok api credentials in .env**:
    ```sh
    #For vonage account users
    APPLICATION_ID=
    PRIVATE_KEY=
    # Note: PRIVATE_KEY can be provided as either:
    # - An absolute path to your application's PEM key file on disk, or
    # - The private key string itself.

    #For opentok account users
    API_KEY=
    API_SECRET=
   
    #Session related info
    WS_URI=wss://
    SESSION_ID=

    #Optional (default value for opentok:"https://api.opentok.com" and vonage: "api.vonage.com")
    API_URL=

    #Do not use any of the fields with quotes like "" just use it without quotes like APPLICATION_ID=sk-proj-toaK2p....
    ```

   **Note:** If authentication variables are not provided (`APPLICATION_ID` + `PRIVATE_KEY` or `API_KEY` + `API_SECRET`), the app still starts the websocket server and prints a warning. In that mode, the app will not attempt to connect with a session, but you can still test the WebSocket server functionality (e.g., using ngrok and a WebSocket client).

## Configure websocket server URLs

   [Optional] If you do not have a public URL to expose your local server, you can use ngrok.
   **Install ngrok**:
   Follow the instructions on the [ngrok website](https://ngrok.com/download) to download and install ngrok.

1. **Start ngrok**:
    In a new terminal, start ngrok to tunnel the local server:

    ```sh
    ngrok http 8765
    #Copy the wss URL, e.g. "uri": "wss://<your-ngrok-domain>",
    ```
    Use https for using with ssl_context.

## Running the Application

**Run the Server application**:

```sh
    python unified_sample_app/sample_app.py
```
The server will start on port 8765. Keep this running while you test with opentok or vonage.

## Usage

**Call the Connect API**:
1. Go to the /unified_sample_app and run the sample_app.py.
2. Start publishing in Playground and then speak. Your audio will reach server and you can speak back with your mic.
