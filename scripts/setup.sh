#!/bin/bash
# Contact-Ops - Setup Script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "========================================"
echo "Contact-Ops - Setup"
echo "========================================"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running from correct directory
if [ ! -f "$PROJECT_DIR/docker-compose.yml" ]; then
    echo -e "${RED}Error: Run this script from the project directory${NC}"
    exit 1
fi

cd "$PROJECT_DIR"

# Step 1: Create .env from example if it doesn't exist
echo -e "\n${YELLOW}Step 1: Environment Configuration${NC}"
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo -e "${GREEN}Created .env from .env.example${NC}"
    echo -e "${YELLOW}Please review and update .env with your settings${NC}"
else
    echo -e "${GREEN}.env already exists${NC}"
fi

# Step 2: Create database
echo -e "\n${YELLOW}Step 2: Database Setup${NC}"
echo "Creating contact_ops_db database..."

docker exec unicorn-postgresql psql -U unicorn -c "CREATE DATABASE contact_ops_db OWNER unicorn;" 2>/dev/null || {
    echo -e "${GREEN}Database already exists or created${NC}"
}

# Step 3: Run database migrations
echo "Running database initialization..."
docker exec -i unicorn-postgresql psql -U unicorn -d contact_ops_db < scripts/init_db.sql

echo -e "${GREEN}Database tables created${NC}"

# Step 4: Build and start services
echo -e "\n${YELLOW}Step 3: Building Services${NC}"

# Development mode
if [ "$1" == "--dev" ] || [ "$1" == "-d" ]; then
    echo "Starting in DEVELOPMENT mode..."
    docker compose build
    docker compose up -d

    echo -e "\n${GREEN}Services started in development mode${NC}"
    echo "Backend: http://localhost:8501"
    echo "Frontend: http://localhost:8502"
    echo "API Docs: http://localhost:8501/docs"

# Production mode
else
    echo "Starting in PRODUCTION mode..."
    docker compose -f docker-compose.prod.yml build
    docker compose -f docker-compose.prod.yml up -d

    echo -e "\n${GREEN}Services started in production mode${NC}"
    echo "Dashboard: https://verify.centerdeep.online"
    echo "API: https://verify.centerdeep.online/api/v1"
fi

# Step 5: Ops-Center integration (optional)
echo -e "\n${YELLOW}Step 4: Ops-Center Integration (Optional)${NC}"
read -p "Add Contact-Ops to Ops-Center Apps Marketplace? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker exec -i unicorn-postgresql psql -U unicorn -d unicorn_db < scripts/ops_center_integration.sql
    echo -e "${GREEN}Added to Ops-Center Apps Marketplace${NC}"
fi

# Step 6: Health check
echo -e "\n${YELLOW}Step 5: Health Check${NC}"
sleep 5

if [ "$1" == "--dev" ] || [ "$1" == "-d" ]; then
    HEALTH_URL="http://localhost:8501/health"
else
    HEALTH_URL="http://localhost:8501/health"
fi

if curl -s "$HEALTH_URL" | grep -q "healthy"; then
    echo -e "${GREEN}Service is healthy!${NC}"
else
    echo -e "${YELLOW}Service may still be starting. Check logs with:${NC}"
    echo "docker compose logs -f contact-ops-backend"
fi

echo -e "\n${GREEN}========================================"
echo "Setup Complete!"
echo "========================================${NC}"
echo ""
echo "Quick Start Commands:"
echo "  View logs:      docker compose logs -f"
echo "  Stop services:  docker compose down"
echo "  Restart:        docker compose restart"
echo ""
echo "Test SMTP verification:"
echo '  curl -X POST http://localhost:8501/api/v1/verify/email \'
echo '    -H "Content-Type: application/json" \'
echo '    -d '"'"'{"email": "REMOVED"}'"'"
echo ""
