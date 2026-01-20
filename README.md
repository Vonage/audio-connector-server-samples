# Vonage Audio Connector Server — Sample Apps

This repository contains sample applications in separate folders. Use them to explore real-time audio streaming with the Vonage Unified Video API and the Audio Connector Server SDK.

## Repository structure

- [`unified_sample_app`](unified_sample_app/README.md) — Demonstrates bidirectional real-time audio over WebSocket, streaming to and from Vonage.
- [`echo_server_app`](echo_server_app/README.md) — Minimal WebSocket server that echoes received audio data for testing.

## Features

- WebSocket server
- Real-time audio streaming to/from Vonage

## Requirements

- Python 3.10+
- Vonage account credentials (application ID and private key), or OpenTok credentials (API key and secret)
- ngrok (or any HTTPS tunnel) for local testing

## Getting started

1. Choose an app folder above and read its local README for detailed setup.
2. Follow the installation and configuration instructions.
3. Run the application and test audio streaming.
