# Echo Server Application - Vonage Audio Connector Server

This is a sample application demonstrating audio streaming using Vonage Audio connector API. The application sets up a WebSocket server that connects to a Vonage session, listens to a publisher’s audio, buffers received audio packets, and when silence is detected, it plays those buffered packets back into the same session.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configure WebSocket server URLs](#configure-websocket-server-urls)
- [Running the Application](#running-the-application)

## Features

- WebSocket server for real-time audio transport
- Voice Activity Detection for silence detection
- Echoes buffered audio back to the session
- Supports Vonage and OpenTok credentials

## Requirements

- Python **3.9+**
- **Vonage account** Need credentials (application-ID, private-key) from vonage account.
- Optional: ngrok for HTTPS tunneling during local testing

## Installation

1. **Set credentials in .env**:
    ```sh
    #For vonage account users
    APPLICATION_ID=
    PRIVATE_KEY=
    # Note: PRIVATE_KEY can be provided as either:
    # - An absolute path to your application's PEM key file on disk, or
    # - The private key string itself.
    
    #Session related info
    WS_URI=wss://

   #Optional if you want to use a existing session otherwise a new session will be created
   SESSION_ID=

    #Optional (default value for vonage: "api.vonage.com")
    API_URL=
    ```

## Configure WebSocket server URLs

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

1. **Install dependencies**:
    ```sh
    pip install -r echo_server_app/requirements.txt
    ```
2. **Create .env**:
    Update the **echo_server_app/env.example** file with your credentials. This will
    export these environment variables in your local terminal session.
    ```sh
    cp echo_server_app/env.example .env
    ```

3. **Run the application**:
    ```sh
    python echo_server_app/echo_server.py
    ```
