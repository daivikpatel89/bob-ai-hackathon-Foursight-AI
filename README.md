# 🛡️ Aegis-HUMS: AI-Driven Military Vehicle Health & Usage Monitoring System

## 👥 Team

| Field | Value |
| :--- | :--- |
| **Team Name** | Foursight AI |
| **Track** | D1: Defense & Aerospace |
| **Team Lead** | Medha Raychura — medha@example.com |
| **Members** | Daivik Patel, Divy Panchal, Siddhi |

---

## 🎯 Problem Statement

Base commanders and field mechanics face immense operational friction when military vehicle sensors trigger complex diagnostic faults. Without immediate, centralized insights, critical repair decisions are delayed, directly compromising mission readiness and asset longevity in the field.

---

## 💡 Solution

Aegis-HUMS is an advanced, AI-powered Health and Usage Monitoring System dashboard built with Streamlit and Python. It integrates a high-performance C++ telemetry generator with the **IBM watsonx Granite 3.0** model (`ibm/granite-3-3-8b-instruct`) to translate raw sensor matrices into instant, authoritative, mission-ready maintenance work orders and executive fleet briefings.

---

## ✨ Key Features

* **Fleet Risk Scoring**: Real-time multi-variable prioritization calculating risk indices from status, temperature, and vibration telemetry.
* **Granite 3.0 AI Explanations**: On-demand per-vehicle diagnostics and fleet-level summaries scoped specifically for a military maintenance commander persona.
* **High-Speed C++ Engine**: Optimized sensor simulation handling thousands of telemetry rows instantly to ensure low-latency dashboard performance.
* **Interactive Telemetry Analytics**: Visual trend graphs tracking engine temperature and component vibration thresholds over time.
* **Production-Ready Configuration**: Secure environment management utilizing `.env` secrets and modular client architecture.

---

## 🛠️ Tech Stack

| Category | Technologies |
| :--- | :--- |
| **Languages** | Python, C++ |
| **Frameworks** | Streamlit, Pandas |
| **IBM Technologies** | watsonx.ai (Granite 3.0 Model), IBM Bob SDK |
| **Databases / Storage** | Local CSV Telemetry (`data/hums_data.csv`) |
| **Other** | GNU GCC, Git, GitHub Actions, Python Dotenv |

---

## ⚡ How to Run

```bash
# 1. Clone the repository
git clone [https://github.com/patelsiddhi07-it/bob-ai-hackathon-Foursight-AI.gif](https://github.com/patelsiddhi07-it/bob-ai-hackathon-Foursight-AI.gif)
cd bob-ai-hackathon-Foursight-AI

# 2. Install dependencies
pip install -r src/requirements.txt

# 3. Configure environment variables
cp src/.env.example src/.env
# (Add your IBM watsonx API key and project ID into src/.env)

# 4. Generate sensor telemetry using C++
mkdir -p data
g++ -std=c++17 -O2 -o hums_gen src/hums_gen.cpp
./hums_gen --rows 5000 --vehicles 20 --output data/hums_data.csv

# 5. Launch the Streamlit dashboard
streamlit run src/app.py -- --csv data/hums_data.csv
