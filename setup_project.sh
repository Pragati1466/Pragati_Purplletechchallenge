#!/bin/bash

# Store Intelligence System - Project Setup Script
# Built for Purplle Tech Challenge 2026

set -e

echo "🎬 Setting up Store Intelligence System..."
echo "Built for Purplle Tech Challenge 2026"
echo ""

# Create directory structure
echo "📁 Creating directory structure..."

# Pipeline directories
mkdir -p pipeline/{models,utils}
mkdir -p output/{events,logs,videos}

# App directories
mkdir -p app/{routers,services,utils}

# Test directories
mkdir -p tests/{unit,integration,fixtures}

# Dashboard directories
mkdir -p dashboard/src/{components,services,utils}
mkdir -p dashboard/public

# Data directories
mkdir -p data/{cctv_clips,layouts,pos}

# Docs directory
mkdir -p docs

echo "✅ Directory structure created"

# Create placeholder files
echo "📝 Creating placeholder files..."

# Pipeline files
touch pipeline/__init__.py
touch pipeline/detect.py
touch pipeline/tracker.py
touch pipeline/reid.py
touch pipeline/zone_classifier.py
touch pipeline/staff_detector.py
touch pipeline/emit.py
touch pipeline/simulate_realtime.py
touch pipeline/run.sh

# App files
touch app/__init__.py
touch app/database.py
touch app/ingestion.py
touch app/metrics.py
touch app/funnel.py
touch app/heatmap.py
touch app/anomalies.py
touch app/health.py

# Test files
touch tests/__init__.py
touch tests/test_pipeline.py
touch tests/test_metrics.py
touch tests/test_funnel.py
touch tests/test_anomalies.py
touch tests/conftest.py

# Make scripts executable
chmod +x pipeline/run.sh
chmod +x setup_project.sh

echo "✅ Placeholder files created"

# Create .env file
echo "🔧 Creating .env file..."
cat > .env << 'EOF'
# Database Configuration
DATABASE_URL=postgresql+asyncpg://storeuser:storepass@localhost:5432/store_intelligence

# Redis Configuration
REDIS_URL=redis://localhost:6379

# API Configuration
API_PORT=8000
LOG_LEVEL=INFO
ENABLE_CORS=true

# Pipeline Configuration
DETECTION_MODEL=yolov8n.pt
CONFIDENCE_THRESHOLD=0.5
REID_THRESHOLD=0.7
STAFF_UNIFORM_COLOR_HSV=160,50,50

# Feature Flags
ENABLE_REALTIME_WEBSOCKET=true
ENABLE_ANOMALY_DETECTION=true
EOF

echo "✅ .env file created"

# Create .gitignore
echo "📝 Creating .gitignore..."
cat > .gitignore << 'EOF'
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
env/
venv/
ENV/
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Testing
.pytest_cache/
.coverage
htmlcov/
*.cover

# IDEs
.vscode/
.idea/
*.swp
*.swo
*~

# Environment
.env
.env.local

# Data
data/cctv_clips/*.mp4
data/cctv_clips/*.avi
output/events/*.jsonl
output/logs/*.log
output/videos/*.mp4

# Models
pipeline/models/*.pt
pipeline/models/*.pth
pipeline/models/*.onnx

# Database
*.db
*.sqlite

# OS
.DS_Store
Thumbs.db

# Docker
docker-compose.override.yml

# Node (for dashboard)
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
EOF

echo "✅ .gitignore created"

# Create README for data directory
echo "📝 Creating data README..."
cat > data/README.md << 'EOF'
# Data Directory

Place your challenge data files here:

## Structure

```
data/
├── cctv_clips/           # CCTV video files
│   ├── STORE_BLR_002/
│   │   ├── entry.mp4
│   │   ├── floor.mp4
│   │   └── billing.mp4
│   └── ...
├── store_layout.json     # Store zone definitions
├── pos_transactions.csv  # POS transaction data
└── sample_events.jsonl   # Sample events for validation
```

## Notes

- Video files are gitignored (too large)
- Only metadata files (JSON, CSV) are tracked
- See main README.md for data specifications
EOF

echo "✅ Data README created"

echo ""
echo "🎉 Project setup complete!"
echo ""
echo "Next steps:"
echo "1. Place your challenge data in data/ directory"
echo "2. Run: docker compose build"
echo "3. Run: docker compose up -d"
echo "4. Run: ./pipeline/run.sh data/cctv_clips/ data/store_layout.json"
echo "5. Open: http://localhost:8000/docs"
echo ""
echo "For detailed instructions, see README.md"
echo ""
echo "Built for Purplle Tech Challenge 2026 🎬✨"
