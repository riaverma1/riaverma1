# Jarvis: Autonomous GitHub Coding Agent - Product Requirements Document

## Executive Summary

Jarvis is an autonomous coding agent that monitors GitHub issues, writes code to address them, and creates pull requests for human review. It provides 24/7 automated software development assistance while maintaining human oversight through mandatory review gates.

## Table of Contents
1. [User Overview](#user-overview)
2. [Technical Specification](#technical-specification)
3. [Security & Guardrails](#security--guardrails)
4. [Architecture Decisions & Trade-offs](#architecture-decisions--trade-offs)
5. [Current Limitations & Gaps](#current-limitations--gaps)
6. [Deployment Requirements](#deployment-requirements)
7. [Next Steps for Production](#next-steps-for-production)
8. [Workflow Diagram](#workflow-diagram)

---

## User Overview

### What It Does (Layman's Terms)

Jarvis is like having a junior developer who:
1. **Watches your GitHub issues** - Automatically detects when you create issues tagged with `jarvis_test`
2. **Writes code to fix them** - Uses AI (Claude) to understand the issue and write appropriate code
3. **Creates pull requests** - Makes a draft PR with the changes for you to review
4. **Never merges automatically** - All changes require human approval before going live
5. **Sends email updates** - Optional notifications when tasks complete

### User Workflow
1. Create a GitHub issue describing what you want built
2. Tag it with `jarvis_test` label
3. Wait 30-60 seconds for Jarvis to pick it up
4. Jarvis creates a branch, writes code, runs tests, and makes a draft PR
5. Review the PR - approve, request changes, or close
6. Merge manually when satisfied

### Benefits
- **Automated processing** - Handles routine coding tasks without manual intervention
- **Consistent code quality** - Follows established patterns
- **Cost effective** - ~$1-3 per task, much cheaper than human developers
- **Safe** - Cannot deploy anything without human review
- **Audit trail** - Full history of all changes and decisions

### Important: Hosting Requirements
**This system requires persistent hosting to run 24/7:**
- **Local development**: Only works while your computer is on and connected
- **Production use**: Requires a server, VM, or cloud hosting that stays online
- **Free options**: Oracle Cloud Free Tier provides permanent free VMs suitable for this system
- **Paid options**: Starting at ~$6/month for reliable cloud hosting

---

## Technical Specification

### System Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   GitHub        │    │    Jarvis        │    │   PostgreSQL    │
│                 │    │   Supervisor     │    │                 │
│ • Issues        │◄──►│ • Task Queue     │◄──►│ • Task State    │
│ • Pull Requests │    │ • Claude Worker  │    │ • Audit Trail   │
│ • Comments      │    │ • PR Monitor     │    │ • Checkpoints   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │
         ▼                       ▼
┌─────────────────┐    ┌──────────────────┐
│   Email/SMTP    │    │   Claude API     │
│                 │    │                  │
│ • Notifications │    │ • Code Generation│
│ • Task Updates  │    │ • Code Analysis  │
└─────────────────┘    └──────────────────┘
```

### Core Components

#### 1. GitHub Issue Monitor (`control_plane/issue_monitor.py`)
- **Purpose**: Polls GitHub API every 60 seconds for new issues
- **Triggers**: Issues with `jarvis_test` label
- **Actions**: Creates task in database, auto-starts execution
- **Trade-off**: Polling vs webhooks (see Architecture Decisions)

#### 2. Task Supervisor (`control_plane/supervisor.py`)
- **Purpose**: LangGraph-based workflow orchestration
- **Flow**: `plan → dispatch → collect → decide → review_gate`
- **Persistence**: Postgres checkpoints survive container restarts
- **Human Gate**: `interrupt()` pauses execution for human decision

#### 3. Claude Code Worker (`control_plane/worker.py`)
- **Purpose**: Executes actual coding work via Claude Code CLI
- **Idempotent**: Safe to replay, won't duplicate work
- **Sandboxed**: Runs in isolated container environment
- **Budget Aware**: Token limits prevent runaway costs

#### 4. PR Monitor (`control_plane/monitor.py`)
- **Purpose**: Watches for human decisions on draft PRs
- **Detection**: Monitors PR comments for `/jarvis` commands
- **Integration**: Resumes workflows based on human feedback

#### 5. Database Layer (`control_plane/db.py`)
- **Table**: `agent_tasks` (renamed from legacy `nightly_task`)
- **Status Flow**: `queued → running → awaiting_review → done/failed/blocked`
- **Migration**: Automatic upgrade from old table name

#### 6. Notification System (`control_plane/notifications.py`)
- **Email Support**: HTML emails via SMTP
- **Triggers**: Task completion, failures, blocking issues
- **Content**: Cost estimates, PR links, change summaries

### Technology Stack

```yaml
Core Technologies:
  - Python 3.12
  - LangGraph (workflow orchestration)
  - PostgreSQL (persistence)
  - Docker & Docker Compose
  - Claude API (AI code generation)
  - GitHub CLI (repository operations)

Dependencies:
  - langgraph>=1.2
  - psycopg[binary,pool]>=3.1
  - python-dotenv>=1.0
  - Claude Code CLI (npm package)

Infrastructure:
  - Container registry (Docker Hub or corporate)
  - SMTP server (Gmail, corporate email)
  - GitHub API access
  - Claude API access
```

### Database Schema

```sql
CREATE TABLE agent_tasks (
    task_id      TEXT PRIMARY KEY,
    source       TEXT NOT NULL,           -- 'github_issue', 'manual'
    source_ref   TEXT,                    -- GitHub issue number
    repo         TEXT NOT NULL,           -- Repository path
    title        TEXT NOT NULL,           -- Human-readable title
    spec         TEXT NOT NULL,           -- Task specification/requirements
    acceptance   TEXT,                    -- Acceptance criteria
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','awaiting_review','blocked','done','failed')),
    thread_id    TEXT,                    -- LangGraph thread identifier
    branch       TEXT,                    -- Git branch name
    pr_url       TEXT,                    -- GitHub PR URL
    token_budget INTEGER NOT NULL DEFAULT 200000,  -- Claude API token limit
    tokens_used  INTEGER NOT NULL DEFAULT 0,       -- Actual tokens consumed
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Security & Guardrails

### Corporate / Enterprise Security Considerations

#### API Gateway Integration (Optional)
- **What it is**: A corporate proxy can be placed in front of the Claude API for routing, monitoring, and access control
- **Standard configuration**: Direct API access via `https://api.anthropic.com`
- **Enterprise configuration**: Route through an internal proxy for audit logging and cost attribution
  ```env
  # Standard (direct)
  ANTHROPIC_BASE_URL=https://api.anthropic.com

  # Enterprise (via internal proxy)
  ANTHROPIC_BASE_URL=https://your-internal-ai-proxy.company.com/
  ```

#### Image Registry Controls
- **Default**: Public Docker Hub images (`postgres:16`, `python:3.12-slim`)
- **Enterprise**: Replace with images from your internal container registry if required by policy

#### Network Restrictions
- **Egress requirements**: Outbound HTTPS to `api.anthropic.com` and `api.github.com`
- **VPN/firewall**: Ensure allowlist rules permit these endpoints from the hosting environment

### Built-in Guardrails

#### 1. Human Review Gate
```python
def review_gate(state: TaskState) -> Command:
    # MANDATORY pause for human approval
    decision = interrupt({
        "ask": "approve | redirect | abandon"
    })
    # No automatic merging ever
```

#### 2. Budget Controls
- **Per-task token limits**: Default 200K tokens (~$30 max)
- **Cost tracking**: Real-time monitoring and alerts
- **Escalation**: Human review triggered on budget overrun

#### 3. Branch Protection
- **Draft PRs only**: Never creates mergeable PRs
- **Separate branches**: `claude/task-{id}` naming pattern
- **No direct pushes**: Cannot push to main/master branches

#### 4. Pre-commit Hooks
- **File validation**: Blocks dangerous file types
- **Code analysis**: Static analysis before commits
- **Secret detection**: Prevents credential leaks

#### 5. Denial Escalation
- **Threshold**: 3 denied operations trigger human review
- **Fail-safe**: System errs on side of caution
- **Audit trail**: All denials logged for review

### Access Controls

#### GitHub Permissions (Minimum Required)
```yaml
Repository Scope: Selected repositories only
Permissions:
  Contents: Write        # Create branches, commit code
  Pull Requests: Write   # Create draft PRs, read comments
  Issues: Read          # Read issue content and labels
  Metadata: Read        # Basic repository access

Explicitly NOT granted:
  Actions: Write        # Cannot modify CI/CD
  Administration: Any   # Cannot change repo settings
  Pages: Any           # Cannot modify GitHub Pages
```

#### Database Access
- **Container-only**: No external database access
- **Encrypted connections**: TLS for all database traffic
- **Credential isolation**: Database passwords in container secrets

---

## Architecture Decisions & Trade-offs

### 1. Polling vs Webhooks for GitHub Issues

**Decision**: Polling every 60 seconds
**Trade-offs**:
- ✅ **Simpler setup**: No webhook endpoint to secure/maintain
- ✅ **Firewall friendly**: Works behind corporate firewalls
- ✅ **More reliable**: No missed events from webhook failures
- ❌ **Slight delay**: 30-60 second detection lag
- ❌ **API usage**: ~1,440 GitHub API calls per day
- ❌ **Not real-time**: Cannot achieve instant response

**Future Alternative**: GitHub App with webhooks for real-time response

### 2. LangGraph vs Custom Orchestration

**Decision**: LangGraph workflow engine
**Trade-offs**:
- ✅ **Durability**: Built-in checkpointing and recovery
- ✅ **Human gates**: Native interrupt/resume support
- ✅ **Visibility**: State machine provides clear workflow tracking
- ✅ **Scalability**: Can handle complex multi-step workflows
- ❌ **Complexity**: Learning curve for team members
- ❌ **Dependency**: Tied to LangChain ecosystem
- ❌ **Overkill**: Simple tasks don't need workflow engine

### 3. Container-based vs Serverless Architecture

**Decision**: Docker containers with PostgreSQL
**Trade-offs**:
- ✅ **Stateful**: Persistent task queue and history
- ✅ **Cost predictable**: Fixed infrastructure costs
- ✅ **Local development**: Easy to run and debug locally
- ✅ **Corporate friendly**: No external cloud dependencies
- ❌ **Always running**: Consumes resources even when idle
- ❌ **Scaling**: Manual scaling vs auto-scaling serverless
- ❌ **Maintenance**: Need to manage container updates

### 4. Email vs Slack Notifications

**Decision**: SMTP email notifications
**Trade-offs**:
- ✅ **Universal**: Everyone has email
- ✅ **Corporate compliant**: Works with any SMTP server
- ✅ **Rich content**: HTML formatting with links
- ✅ **Async**: Non-intrusive notifications
- ❌ **Not real-time**: Email delays possible
- ❌ **Limited interaction**: No buttons/responses in email
- ❌ **SMTP complexity**: Requires SMTP server configuration

### 5. Claude Code vs Direct API Integration

**Decision**: Use Claude Code CLI tool
**Trade-offs**:
- ✅ **Battle tested**: Mature tool with good practices
- ✅ **Git integration**: Handles branch/commit operations
- ✅ **Error handling**: Robust retry and recovery logic
- ✅ **Tool use**: Supports code execution and testing
- ❌ **Black box**: Less control over AI interactions
- ❌ **Dependency**: Must install and maintain CLI tool
- ❌ **Performance**: Extra process overhead

---

## Current Limitations & Gaps

### 0. Hosting Requirement (Critical Understanding)
**Current**: Requires persistent server/VM to run 24/7
**Reality Check**:
- Does NOT work when your laptop sleeps or is shut down
- Containers stop when Docker host stops
- GitHub monitoring stops when system is offline
- Requires always-on infrastructure for true "autonomous" operation

**Solutions**:
- Free: Oracle Cloud Forever Free Tier (4 cores, 24GB RAM - permanent)
- Paid: DigitalOcean droplets starting at $6/month
- Corporate: Internal VM or cloud hosting

### 1. Single Repository Support
**Current**: Only operates on one repository at a time
**Impact**: Cannot handle cross-repo dependencies
**Future Need**: Multi-repo mounting and coordination

### 2. No Evaluation Metrics
**Current**: No systematic quality assessment
**Missing**:
- Code quality scores
- Test coverage impact
- Performance regression detection
- Success rate analytics

### 3. Limited Test Integration
**Current**: Basic test execution via Claude Code
**Missing**:
- Integration with CI/CD pipelines
- Advanced test frameworks
- Performance testing
- Security scanning

### 4. Basic Cost Controls
**Current**: Simple token budgets per task
**Missing**:
- Organization-wide budgets
- Cost allocation by team/project
- Predictive cost modeling
- Usage analytics dashboard

### 5. Primitive Error Handling
**Current**: Basic retry with human escalation
**Missing**:
- Intelligent error classification
- Automatic recovery strategies
- Error pattern learning
- Diagnostic tools

### 6. No Advanced Scheduling
**Current**: Immediate processing only
**Missing**:
- Priority queues
- Time-based scheduling
- Resource-aware scheduling
- Load balancing

### 7. Limited Monitoring
**Current**: Basic logging and email alerts
**Missing**:
- Metrics dashboard
- Performance monitoring
- Health checks
- SLA tracking

---

## Deployment Requirements

### Infrastructure Needs

#### For 24/7 Production Operation

**Enterprise Requirements:**
```yaml
Compute:
  CPU: 2-4 cores minimum
  RAM: 8GB minimum (PostgreSQL + containers)
  Storage: 100GB+ for logs, database, git repos
  Network: Stable internet with GitHub/Claude API access

High Availability:
  Load Balancer: Multiple container instances
  Database: PostgreSQL with replication
  Monitoring: Prometheus + Grafana stack
  Alerting: PagerDuty integration

Backup Strategy:
  Database: Daily automated backups
  Configuration: Infrastructure as Code (Terraform)
  Secrets: Vault or similar secret management
```

**Individual/Small Team Options:**

**Free Option - Oracle Cloud Forever Free Tier:**
```yaml
Specs:
  VM Shape: VM.Standard.A1.Flex (ARM-based)
  CPU: 4 OCPU cores (can allocate to single instance)
  RAM: 24GB (can allocate to single instance)
  Storage: 200GB block volume
  Network: Always-free public IP

Setup:
  1. Create Oracle Cloud account (credit card required for verification)
  2. Create ARM-based compute instance
  3. Install Docker and Docker Compose
  4. Deploy Jarvis stack
  5. Configure domain/DNS (optional)

Cost: $0/month forever (Oracle's commitment)
Reliability: Good uptime, enterprise-grade infrastructure
```

**Low-Cost Option - DigitalOcean:**
```yaml
Droplet:
  Size: Basic ($6/month)
  CPU: 1 vCPU
  RAM: 1GB
  Storage: 25GB SSD
  Transfer: 1TB

Additional:
  Managed PostgreSQL: +$15/month (optional, can run in container)
  Load Balancer: +$12/month (for production)
  Backup: +20% of droplet cost

Total: $6-35/month depending on features
```

#### Cloud Deployment Options

**Option 1: AWS ECS**
```yaml
Services:
  ECS Tasks: For containerized services
  RDS PostgreSQL: Managed database
  Application Load Balancer: Traffic distribution
  CloudWatch: Monitoring and alerting
  Secrets Manager: Credential management
```

**Option 2: Kubernetes**
```yaml
Resources:
  Deployments: For scalable containers
  StatefulSet: For PostgreSQL
  ConfigMaps/Secrets: Configuration management
  Ingress: External access
  PersistentVolumes: Database storage
```

**Option 3: Oracle Cloud Free Tier** ⭐ (Individual Developers)
```yaml
Forever Free Resources:
  Compute: 4 ARM OCPU cores, 24GB RAM (can be single VM)
  Storage: 200GB block volumes
  Network: 10TB egress per month
  Database: Autonomous Database options available

Perfect for:
  Running Jarvis 24/7 at zero cost
  Learning and development
  Small team usage (1-10 developers)

Limitations:
  ARM architecture (most software works fine)
  Single region selection
  No guaranteed SLA (but generally reliable)
```

**Option 4: DigitalOcean Droplets** (Paid but simple)
```yaml
Setup:
  Droplet: 1GB+ memory ($6/month), Docker pre-installed
  Managed PostgreSQL: Optional database service (+$15/month)
  Load Balancer: Optional DigitalOcean LB (+$12/month)
  Spaces: Backup storage
  Monitoring: Built-in metrics
```

### Environment Configuration

#### Production .env Template
```bash
# Claude API Configuration
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_AUTH_TOKEN=your_claude_api_key
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# GitHub Integration
GITHUB_TOKEN=your_github_app_token
GITHUB_REPO=your_org/your_repo

# Database (managed service recommended)
JARVIS_DSN=postgresql://user:pass@prod-db:5432/jarvis

# Email Notifications
NOTIFICATION_EMAIL=alerts@company.com
SMTP_SERVER=smtp.company.com
SMTP_USERNAME=jarvis@company.com
SMTP_PASSWORD=app_password

# Operational Settings
JARVIS_AUTO_RUN=true
JARVIS_MAX_CONCURRENT=3
MONITOR_POLL_INTERVAL=30
```

---

## Next Steps for Production

### Phase 1: Core Stability (4-6 weeks)
1. **Comprehensive Testing**
   - Unit tests for all components
   - Integration tests with real GitHub repos
   - Load testing with multiple concurrent tasks
   - Failure recovery testing

2. **Enhanced Error Handling**
   - Structured error classification
   - Automatic retry strategies
   - Better error reporting to users
   - Graceful degradation modes

3. **Production Monitoring**
   - Health check endpoints
   - Metrics collection (Prometheus)
   - Alerting rules (PagerDuty)
   - Performance dashboards

### Phase 2: Enterprise Features (6-8 weeks)
1. **Multi-Repository Support**
   - Repository discovery and mounting
   - Cross-repo dependency handling
   - Repository-specific configurations
   - Access control per repository

2. **Advanced Security**
   - OAuth integration for GitHub
   - Role-based access control
   - Audit logging and compliance
   - Secret rotation automation

3. **Cost Management**
   - Budget alerts and controls
   - Usage analytics dashboard
   - Cost allocation by team
   - Predictive cost modeling

### Phase 3: Scale & Intelligence (8-12 weeks)
1. **Evaluation Framework**
   - Code quality assessment
   - Automated testing integration
   - Success rate tracking
   - Performance impact analysis

2. **Advanced Scheduling**
   - Priority-based task queues
   - Resource-aware scheduling
   - Time-window restrictions
   - Load balancing

3. **Learning & Optimization**
   - Pattern recognition in failures
   - Automated parameter tuning
   - Knowledge base accumulation
   - User preference learning

---

## Workflow Diagram

```mermaid
sequenceDiagram
    participant User
    participant GitHub
    participant IssueMonitor
    participant Supervisor
    participant ClaudeWorker
    participant Database
    participant EmailSystem

    User->>GitHub: Create issue with 'jarvis_test' label

    loop Every 60 seconds
        IssueMonitor->>GitHub: Poll for new labeled issues
    end

    GitHub-->>IssueMonitor: Return new issues
    IssueMonitor->>Database: Create task record
    IssueMonitor->>Supervisor: Start task execution

    Supervisor->>Database: Update status to 'running'
    Supervisor->>ClaudeWorker: Dispatch coding task

    ClaudeWorker->>ClaudeWorker: Generate code
    ClaudeWorker->>GitHub: Create branch
    ClaudeWorker->>GitHub: Commit changes
    ClaudeWorker->>GitHub: Run tests
    ClaudeWorker->>GitHub: Create draft PR
    ClaudeWorker-->>Supervisor: Return results

    Supervisor->>Database: Update with PR details
    Supervisor->>Supervisor: Interrupt for human review
    Supervisor->>EmailSystem: Send completion notification
    EmailSystem->>User: Email with PR link

    User->>GitHub: Review draft PR

    alt User approves
        User->>GitHub: Add approval comment
        GitHub-->>Supervisor: Detect approval
        Supervisor->>Database: Mark as 'done'
        User->>GitHub: Merge PR manually
    else User requests changes
        User->>GitHub: Add feedback comment
        GitHub-->>Supervisor: Detect feedback
        Supervisor->>ClaudeWorker: Re-dispatch with guidance
    else User abandons
        User->>GitHub: Close PR
        GitHub-->>Supervisor: Detect closure
        Supervisor->>Database: Mark as 'failed'
    end
```

### Detailed Flow Breakdown

#### 1. Issue Detection (30-60 second latency)
- Issue monitor polls GitHub API
- Filters for `jarvis_test` labeled issues
- Creates database task record
- Triggers immediate execution (if auto-run enabled)

#### 2. Task Execution (1-5 minutes typical)
- LangGraph supervisor orchestrates workflow
- Claude worker analyzes issue requirements
- Generates code using Claude API
- Creates git branch with naming pattern `claude/issue-{number}`
- Commits changes with descriptive messages
- Runs tests to validate functionality

#### 3. PR Creation (30 seconds)
- Creates draft pull request on GitHub
- Links back to original issue
- Includes summary of changes made
- Never creates mergeable PR (safety measure)

#### 4. Human Review Gate (Manual timing)
- System pauses and waits for human decision
- Sends email notification with PR link
- Monitors PR for approval/feedback comments
- State persisted in database across restarts

#### 5. Resolution
- **Approve**: Task marked complete, user merges manually
- **Redirect**: New guidance provided, re-runs with additional context
- **Abandon**: Task marked failed, PR closed

### Critical Success Factors

1. **Response Time**: Issue → PR creation in under 5 minutes
2. **Reliability**: 99%+ uptime for core services
3. **Safety**: Zero unauthorized merges to main branches
4. **Cost Control**: <$5 average cost per task
5. **User Experience**: Clear status communication throughout

### Failure Modes & Recovery

1. **GitHub API Limits**: Exponential backoff with retries
2. **Claude API Errors**: Graceful degradation with human notification
3. **Database Failures**: Automatic restart with persistent state
4. **Network Issues**: Retry logic with extended timeouts
5. **Container Crashes**: Kubernetes/Docker restart policies
