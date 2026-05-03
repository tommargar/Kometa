# GEMINI.MD: AI Collaboration Guide

This document provides essential context for AI models interacting with this project. Adhering to these guidelines will ensure consistency and maintain code quality.

## 1. Project Overview & Purpose

* **Primary Goal:** Kometa (Emby Adaption) is a Python-based automation tool designed to manage metadata, collections, and overlays for the Emby media server. It is an adaptation of the original Kometa (formerly Plex Meta Manager) project, specifically tailored for Emby integration.
* **Business Domain:** Media Server Automation, Home Lab, Digital Asset Management.

## 2. Core Technologies & Stack

* **Languages:** Python 3.11+
* **Frameworks & Runtimes:** Python Runtime, Docker (for deployment).
* **Databases:** Interacts with Emby's internal database via API; utilizes YAML for state and configuration management.
* **Key Libraries/Dependencies:**
    *   `requests`: For HTTP API communication with Emby, TMDB, TVDB, etc.
    *   `ruamel.yaml` / `PyYAML`: For parsing complex YAML configuration files.
    *   `furl`: For URL manipulation.
    *   `pylast`: For Last.fm integration.
* **Package Manager(s):** `pip` (Python), Docker.

## 3. Architectural Patterns

* **Overall Architecture:** Config-Driven Automation. The application's behavior is entirely dictated by user-defined YAML files. It operates as a client interacting with various third-party APIs (Emby, TMDB, Trakt, etc.).
* **Directory Structure Philosophy:**
    *   `/config`: Contains user configuration files (`config.yml`) and generated reports.
    *   `/modules`: Core application logic.
        *   `/modules/emby.py`: Specific logic for Emby API interaction.
        *   `/modules/emby_server.py`: Logic for managing the Emby Server connection.
    *   `/defaults`: Pre-packaged Collection and Overlay YAML configurations.
    *   `/docs`: Documentation source files (MkDocs).

## 4. Coding Conventions & Style Guide

* **Formatting:** Code must be formatted using **Black**.
* **Naming Conventions:**
    *   Variables/Functions: `snake_case`
    *   Classes: `PascalCase`
* **API Design:** The project consumes the Emby API. Adherence to Emby's Swagger documentation is critical. Note that Emby's ID structure and endpoints differ significantly from Plex. The docs can be found in `/__Emby SDK/doc` and its subfolders 
* **Error Handling:**
    *   **No bare `try-except` blocks.**
    *   Exceptions must be logged using the internal logging module to provide user-visible context (e.g., identifying the specific YAML line causing an error).
* **Typing:** Strict usage of Python **Type Hints** (`from typing import ...`) is required throughout the codebase.
* **Logging:** Use the internal Kometa logging module. **Do not use `print()`**.

## 5. Key Files & Entrypoints

* **Main Entrypoint(s):** The application is typically executed via Docker entrypoint or a main Python script wrapper.
* **Configuration:**
    *   `config/config.yml`: The central configuration file defining server connections and library mappings.
    *   `defaults/`: Contains standard templates for collections and overlays.
* **CI/CD Pipeline:** Docker images are built and pushed to repositories (e.g., `kometateam/kometa`).

## 6. Development & Testing Workflow

* **Local Development Environment:** Setup involves creating a Python virtual environment (`venv`), installing dependencies via `pip install -r requirements.txt`, or running via Docker with volume mounts for config.
* **Testing:** Development relies on verifying YAML parsing accuracy and API interaction validity.

## 7. Specific Instructions for AI Collaboration

* **Emby Specificity:** Focus on `modules/emby.py` and `modules/emby_server.py`. Ignore `plex.py` logic unless creating abstract base classes.
* **YAML Structure:** Generated code must respect the existing YAML schema used by Kometa. The logic must be robust against user configuration errors.
* **API Accuracy:** Verify all Emby API endpoints. Do not hallucinate endpoints based on Plex logic; they are distinct ecosystems.
* **Performance:** Optimize for API rate limits. Avoid iterating over entire libraries; utilize Emby's filter queries to fetch specific data subsets.
* **Documentation:** Ensure any suggested configuration changes align with the documentation in `docs/`.
* **Conversation:** Please answer in German
* **Emby Architecture to consider:** Emby has currently no AutoCollection or Smart Playlist, thus all Kometa functionality in this regard has to be emulated. 
* **Rating Field Specifics:** As the Emby field UserRating is used for age certification, the new field CustomRating in the ProviderIds was used instead for storing the Plex user rating. Please also consider the correct scaling and storage for the Emby rating fields, with 'CriticRating' ranging from 0-100 int displayed as percent. 'CommunityRating' a and 'UserRating' in ProviderIds use 0-10 float displayed as * rating.