# Contributing to NSA Legal Intelligence Platform

Thank you for your interest in contributing to the NSA Legal Intelligence Platform! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Coding Standards](#coding-standards)
- [Pull Request Process](#pull-request-process)
- [Testing](#testing)
- [Security](#security)

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Getting Started

1. Fork the repository
2. Clone your fork locally:

   ```bash
   git clone https://github.com/your-username/NSA_webservice.git
   cd NSA_webservice
   ```

3. Create a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

4. Install dependencies:

   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt  # For development
   ```

## Development Setup

### Windows Development

This project supports native Windows development without Docker:

1. **Python**: Install Python 3.12 from [python.org](https://python.org)
2. **PostgreSQL**: Install from [postgresql.org](https://www.postgresql.org/download/windows/)
3. **Neo4j Desktop**: Download from [neo4j.com](https://neo4j.com/download/)
4. **Qdrant**: Use Qdrant Cloud (preferred) or download native executable
5. **Redis**: Use Memurai or Redis for Windows

### Environment Configuration

1. Copy `.env.example` to `.env`:

   ```bash
   cp .env.example .env
   ```

2. Update `.env` with your local development values
3. Never commit `.env` to version control

### Running the Application

```bash
# Set FLASK_APP and run migrations
export FLASK_APP=app:create_app
flask db upgrade

# Run the development server
flask run

# Or with Gunicorn
gunicorn --bind 0.0.0.0:8000 app:app
```

## Coding Standards

### Python Style Guide

- Follow [PEP 8](https://peps.python.org/pep-0008/)
- Use [Black](https://github.com/psf/black) for formatting
- Use [Ruff](https://github.com/astral-sh/ruff) for linting
- Type hints are recommended

### Pre-commit Hooks

Install pre-commit hooks before making changes:

```bash
pip install pre-commit
pre-commit install
```

This ensures code quality checks run automatically on commit.

### Commit Messages

Write clear, descriptive commit messages:

- Use present tense ("Add feature" not "Added feature")
- Use imperative mood ("Move cursor to..." not "Moves cursor to...")
- Limit the first line to 72 characters
- Reference issues and pull requests liberally after the first line

Example:

```
fix: correct KMC TLS certificate validation

Remove insecure SSL settings that disabled certificate verification.
This prevents MITM attacks on KMC license lookups.

Fixes #123
```

## Pull Request Process

1. Create a feature branch from `main`:

   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes and commit

3. Run tests:

   ```bash
   pytest tests/
   ```

4. Ensure all pre-commit checks pass:

   ```bash
   pre-commit run --all-files
   ```

5. Push to your fork and create a PR

6. Request review from maintainers

### PR Checklist

- [ ] Tests pass
- [ ] Code follows style guidelines
- [ ] Documentation updated (if needed)
- [ ] CHANGELOG.md updated (if applicable)
- [ ] Security implications considered
- [ ] No sensitive data in code

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_step1.py

# Run specific test
pytest tests/test_step1.py::TestFSOModel::test_fso_model_structure
```

### Writing Tests

- Place tests in the `tests/` directory
- Use descriptive test names
- Follow the existing test patterns
- Mock external dependencies when appropriate

## Security

### Reporting Security Issues

See [SECURITY.md](SECURITY.md) for security reporting guidelines.

### Security Best Practices

- Never commit secrets or credentials
- Use environment variables for sensitive data
- Review code for security implications before merging
- Keep dependencies updated

---

Thank you for contributing to making this project better!
