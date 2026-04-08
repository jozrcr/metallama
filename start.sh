#!/bin/bash

uvicorn app.main:app --app-dir metallama --host 0.0.0.0 --port 8010 --reload
