# Integrated Employee Experience Assessment

A production-ready Flask web app that collects 95-item employee assessment responses, auto-scores them across 8 domains and 60+ subdomains, and stores everything in PostgreSQL.

## Features

- **95-question assessment** with section-based UI, live progress tracking, and mobile-friendly design
- **Auto-scoring** with reverse-coding for flagged items (Q3, Q10, Q37, Q45, Q66, Q72-76, Q81, Q88-89, Q92-94)
- **PostgreSQL storage** using SQLAlchemy with JSONB columns for flexible querying
- **Admin endpoints** for stats and CSV export (token-protected)
- **Health check** endpoint for AWS load balancers
- **Dockerized** and ready for AWS Elastic Beanstalk, ECS, or EC2

## Project structure

```
assessment_app/
├── app.py              # Flask app, routes, scoring logic
├── questions.py        # All 95 questions + scoring rubric
├── init_db.py          # One-time table creation
├── templates/
│   ├── survey.html     # Survey form
│   └── thank_you.html  # Confirmation page
├── requirements.txt
├── Dockerfile
├── .env.example
└── README.md
```

## Local setup

### 1. Install PostgreSQL locally (or use Docker)
```bash
# macOS
brew install postgresql && brew services start postgresql
createdb assessment_db

# Ubuntu/Debian
sudo apt install postgresql postgresql-contrib
sudo -u postgres createdb assessment_db
```

### 2. Clone and install Python deps
```bash
cd assessment_app
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env with your DATABASE_URL, SECRET_KEY, ADMIN_TOKEN
# Generate secrets:
python -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
python -c "import secrets; print('ADMIN_TOKEN=' + secrets.token_urlsafe(24))"
```

### 4. Create database tables
```bash
python init_db.py
```

### 5. Run the app
```bash
# Development
python app.py

# Production (gunicorn)
gunicorn --bind 0.0.0.0:5000 --workers 4 app:app
```

Open http://localhost:5000 in your browser.

## Endpoints

| Route                              | Purpose                                     |
|------------------------------------|---------------------------------------------|
| `GET  /`                           | Survey form                                 |
| `POST /submit`                     | Submit responses                            |
| `GET  /thank-you`                  | Confirmation page                           |
| `GET  /health`                     | Health check (used by AWS)                  |
| `GET  /admin/stats?token=XXX`      | Aggregated stats (JSON)                     |
| `GET  /admin/export.csv?token=XXX` | Full CSV export of all responses + scores   |

## AWS Deployment

### Option A: Elastic Beanstalk (easiest)

1. **Create RDS PostgreSQL database**
   - AWS Console → RDS → Create database
   - Engine: PostgreSQL 16
   - Template: Free tier (db.t4g.micro) for testing
   - Set master username/password
   - Public access: No (use VPC)
   - Note the endpoint URL

2. **Install EB CLI**
   ```bash
   pip install awsebcli
   ```

3. **Initialize and deploy**
   ```bash
   cd assessment_app
   eb init -p python-3.11 assessment-app --region us-east-1
   eb create assessment-env --database.engine postgres
   # Or if RDS already exists:
   eb create assessment-env
   ```

4. **Set environment variables**
   ```bash
   eb setenv \
     SECRET_KEY="your-secret-key" \
     DATABASE_URL="postgresql://USER:PASS@your-rds-endpoint:5432/assessment_db" \
     ADMIN_TOKEN="your-admin-token"
   ```

5. **Initialize DB schema (one-time)**
   ```bash
   eb ssh
   cd /var/app/current
   source /var/app/venv/*/bin/activate
   python init_db.py
   exit
   ```

6. **Open the app**
   ```bash
   eb open
   ```

### Option B: EC2 + Docker (more control)

1. **Launch EC2 instance** (Ubuntu 22.04, t3.small) and SSH in
2. **Install Docker**:
   ```bash
   sudo apt update && sudo apt install -y docker.io
   sudo usermod -aG docker $USER && newgrp docker
   ```
3. **Copy this project** to the instance (scp or git clone)
4. **Create `.env` file** with your RDS connection string
5. **Build and run**:
   ```bash
   docker build -t assessment-app .
   docker run -d --name assessment --env-file .env -p 80:8080 --restart unless-stopped assessment-app
   docker exec assessment python init_db.py
   ```
6. **Open security group** to allow port 80 from anywhere (0.0.0.0/0)
7. Visit `http://<ec2-public-ip>` in your browser

### Option C: ECS Fargate (serverless containers)

1. Push image to ECR:
   ```bash
   aws ecr create-repository --repository-name assessment-app
   docker tag assessment-app:latest <account>.dkr.ecr.<region>.amazonaws.com/assessment-app:latest
   aws ecr get-login-password | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
   docker push <account>.dkr.ecr.<region>.amazonaws.com/assessment-app:latest
   ```
2. Create an ECS Fargate cluster → Task definition → Service
3. Use environment variables for SECRET_KEY, DATABASE_URL, ADMIN_TOKEN
4. Attach Application Load Balancer with health check on `/health`

## Production checklist

- [ ] Use a strong random SECRET_KEY (32+ bytes)
- [ ] Use a strong random ADMIN_TOKEN (24+ bytes)
- [ ] Enable HTTPS (use AWS ACM + ALB, or Let's Encrypt + nginx on EC2)
- [ ] Restrict RDS security group to only the EB/EC2 security group (not public)
- [ ] Enable RDS automated backups
- [ ] Set up CloudWatch alarms for errors and DB connection counts
- [ ] Test the `/admin/export.csv` endpoint before sending out the survey link
- [ ] Add rate limiting (Flask-Limiter) if exposing publicly to prevent spam
- [ ] Consider adding a CAPTCHA or referrer check to prevent bot submissions

## Querying the data directly

If you want to run analyses in SQL:

```sql
-- Count responses per day
SELECT DATE(submitted_at), COUNT(*) FROM responses GROUP BY 1 ORDER BY 1;

-- Average score per subdomain
SELECT
  jsonb_object_keys(scores) AS subdomain,
  AVG((scores->jsonb_object_keys(scores)->>'raw_score')::int) AS avg_score
FROM responses
GROUP BY 1
ORDER BY 2 DESC;

-- High-stress responders (Perceived Stress subdomain in "high" bucket)
SELECT id, submitted_at, scores->'Perceived Stress'->>'raw_score' AS stress_score
FROM responses
WHERE scores->'Perceived Stress'->>'level' = 'high';
```

## License

Use freely.
