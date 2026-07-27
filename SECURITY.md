# Security Policy

## Supported Versions

| Version | Supported |
|---------|----------|
| 1.x.x   | ✅ Yes    |
| < 1.0   | ❌ No     |

## Reporting a Vulnerability

We take the security of the NSA Legal Intelligence Platform seriously. If you believe you have found a security vulnerability, please report it to us through one of the following methods:

### Preferred Method

Email: **<security@nsa-lip.org>** (monitored by security team)

### Alternative Method

GitHub Security Advisory: Use the "Report a Security Vulnerability" button in our GitHub repository.

## What to Include

When reporting a vulnerability, please include:

1. **Description**: A clear description of the vulnerability
2. **Steps to Reproduce**: Detailed steps to reproduce the issue
3. **Impact**: What an attacker could achieve
4. **Environment**: Version of the software, operating system, etc.
5. **Proof of Concept**: (if possible) a proof of concept exploit

## Response Timeline

- **Initial Response**: Within 48 hours
- **Acknowledgment**: We will acknowledge receipt of your report
- **Investigation**: We will investigate and validate the vulnerability
- **Fix**: We will develop a fix if the vulnerability is confirmed
- **Disclosure**: Coordinated disclosure after the fix is available

## Security Best Practices

### For Developers

- Never commit secrets, API keys, or credentials to version control
- Use environment variables for sensitive configuration
- Keep dependencies updated
- Follow OWASP Top 10 guidelines
- Validate and sanitize all user inputs
- Use parameterized queries to prevent SQL injection
- Implement proper authentication and authorization

### For Deployers

- Use HTTPS for all communications
- Set strong SECRET_KEY for sessions
- Configure proper CORS policies
- Enable security headers (CSP, HSTS, X-Frame-Options)
- Run with least privilege
- Keep the system updated

## Known Security Considerations

### TLS Configuration

- KMC government portal requires SECLEVEL=1 for older certificate compatibility
- Certificate verification is enforced (no MITM protection)
- HTTPS is enforced in production

### Session Security

- Session cookies are Secure, HttpOnly, and SameSite=Lax
- Session lifetime is 30 minutes
- Sessions are refreshed on each request

### CSRF Protection

- All POST forms require CSRF tokens
- AJAX requests include CSRF tokens

### Authentication

- Passwords are hashed using werkzeug.security
- Login attempts are logged
- Failed login attempts are tracked

## Security Features

### Implemented

- ✅ Flask-Talisman (CSP, HSTS, HTTPS enforcement)
- ✅ Flask-WTF (CSRF protection)
- ✅ Flask-Login (session management)
- ✅ SQLAlchemy versioning (optimistic locking)
- ✅ Audit logging (RecordAudit table)
- ✅ Rate limiting (KMC portal: 40s minimum between requests)

### Planned

- [ ] Multi-factor authentication
- [ ] Role-based access control
- [ ] API key management
- [ ] Security event monitoring
- [ ] Automated security scanning

## Contact

For security concerns, contact:

**Primary**: <security@nsa-lip.org>  
**Backup**: <sumansaha9@hotmail.com>

## Disclosure Policy

We follow responsible disclosure:

1. Report the vulnerability to us
2. Give us reasonable time to investigate and fix
3. Coordinate on disclosure timing
4. Credit the researcher (if desired)

We ask that you:

- Do not exploit the vulnerability
- Do not disclose it publicly until we've fixed it
- Be patient and cooperative
