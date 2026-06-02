#!/bin/bash
# Run broadcast_campaign.py inside sales-bot container (has env + DB access)
set -e
CAMPAIGN="$1"
shift
docker exec charoenpon-sales-bot python3 -c "
import os, sys, asyncio
sys.path.insert(0, \"/app\")
sys.argv = [\"broadcast\", \"$CAMPAIGN\"] + \"\"\"$@\"\"\".split()
import scripts.broadcast_campaign as bc
def fake_env(): return dict(os.environ)
bc.load_env = fake_env
asyncio.run(bc.main())
"
