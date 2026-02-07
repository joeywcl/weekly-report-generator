# Deployment Guide

## Version Management

### Manual Version Bump
```bash
# Patch version (1.0.0 → 1.0.1)
./bump_version.sh patch

# Minor version (1.0.1 → 1.1.0)
./bump_version.sh minor

# Major version (1.1.0 → 2.0.0)
./bump_version.sh major

# Then commit and tag
git add VERSION
git commit -m "chore: bump version to $(cat VERSION)"
git tag v$(cat VERSION)
git push origin main --tags
```

### After Merge to Main
```bash
# 1. Bump version
./bump_version.sh patch

# 2. Deploy (choose your method below)
```

---

## Deployment Options

### Option 1: Current SSH/VM (Current Setup)
**Cost:** VM cost (~$5-50/month depending on provider)

```bash
# On VM
cd /path/to/weekly
git pull origin main
pip install -r requirements.txt
sudo systemctl restart weekly-report
```

**Pros:**
- ✅ Simple, you already have it
- ✅ Full control

**Cons:**
- ❌ VM costs money even when idle
- ❌ Manual updates

---

### Option 2: Docker on VM (Recommended)
**Cost:** Same VM, but easier to manage

```bash
# On VM (one-time setup)
git pull origin main
docker-compose up -d

# Future updates
git pull origin main
docker-compose up -d --build
```

**Pros:**
- ✅ Consistent environment
- ✅ Easy rollbacks
- ✅ Simple updates

**Cons:**
- ❌ Still costs VM money

---

### Option 3: Free Hosting Options

#### **A. Render.com (RECOMMENDED - Free Tier)**
**Cost:** FREE (with limitations)
- ✅ Free for web services
- ✅ Auto-deploy from GitHub
- ✅ HTTPS included
- ⚠️ Sleeps after 15min idle (cold start ~30s)

**Setup:**
1. Go to render.com
2. Connect GitHub repo
3. Create Web Service
4. Set:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn -w 2 -b 0.0.0.0:$PORT app:app`
   - Add env var: `OPENAI_API_KEY`
5. Deploy!

**Custom domain:** Add `weekly.rda.ai` in Render settings

---

#### **B. Railway.app**
**Cost:** FREE $5/month credit (enough for small apps)
- ✅ No sleep/cold start
- ✅ Auto-deploy from GitHub
- ✅ HTTPS included

**Setup:**
1. Go to railway.app
2. "New Project" → Import from GitHub
3. Add environment variable: `OPENAI_API_KEY`
4. Deploy!

---

#### **C. Fly.io**
**Cost:** FREE tier (3 small VMs)
- ✅ Global deployment
- ✅ Always on (no sleep)
- ✅ HTTPS + custom domains

**Setup:**
```bash
# Install flyctl
curl -L https://fly.io/install.sh | sh

# Login and launch
flyctl auth login
flyctl launch

# Set secrets
flyctl secrets set OPENAI_API_KEY=sk-...

# Deploy
flyctl deploy
```

---

### Option 4: Serverless (Most Cost-Effective)

#### **Vercel (Free)**
**Cost:** FREE
- ✅ Free forever for hobby
- ✅ Auto-deploy from GitHub
- ✅ HTTPS + custom domains
- ⚠️ Serverless (stateless, no file writes)

**Setup:**
1. Install Vercel CLI: `npm i -g vercel`
2. Add `vercel.json`:
```json
{
  "builds": [{ "src": "app.py", "use": "@vercel/python" }],
  "routes": [{ "src": "/(.*)", "dest": "app.py" }]
}
```
3. `vercel --prod`

---

## Cost Comparison

| Option | Monthly Cost | Uptime | Setup | Updates |
|--------|-------------|--------|-------|---------|
| **Current VM** | $10-50 | 24/7 | Done | Manual |
| **Docker on VM** | $10-50 | 24/7 | Easy | Easy |
| **Render.com** | FREE | 24/7 (sleeps) | 5min | Auto |
| **Railway** | FREE ($5 credit) | 24/7 | 5min | Auto |
| **Fly.io** | FREE | 24/7 | 10min | Easy |
| **Vercel** | FREE | 24/7 | 10min | Auto |

---

## My Recommendation

**For `weekly.rda.ai`:**

### **Best Option: Railway.app** 🏆
**Why:**
- ✅ FREE (enough for your traffic)
- ✅ No cold starts (always fast)
- ✅ Auto-deploy from GitHub
- ✅ Custom domain support
- ✅ Easy rollbacks

**Migration steps:**
1. Sign up at railway.app
2. Import your GitHub repo
3. Set `OPENAI_API_KEY` env var
4. Update DNS: `weekly.rda.ai` → Railway's URL
5. Cancel VM (save $10-50/month)

**Alternative: Render.com** if you don't mind 30s cold start (100% free forever)

---

## Auto-Deploy Workflow (GitHub Actions)

Add this to `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Railway

on:
  push:
    branches: [main]
    tags: ['v*']

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to Railway
        uses: bervProject/railway-deploy@main
        with:
          railway_token: ${{ secrets.RAILWAY_TOKEN }}
          service: weekly-report
```

Then every merge to main = auto-deploy! 🚀
