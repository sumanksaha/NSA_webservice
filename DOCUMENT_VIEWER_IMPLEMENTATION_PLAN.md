# Document Web Viewer & Editor Implementation - Research & Implementation Plan

## Overview

This document outlines the research-based implementation plan for creating an HTML-based web viewer with editing capabilities for document files (PDF/DOCX/TXT) instead of direct downloads. The solution maintains existing backend architecture while adding modern web-based document management.

## Current Architecture Analysis

### Existing Document Processing Flow

```
PDF/DOCX/TXT Files
     ↓
DocumentLoaderFactory (Text Extraction)
     ↓
DocumentResult (Pages + Metadata)
     ↓
Text Content → Legal Templates → WeasyPrint → PDF Generation
```

### Key Components in Current System

**Document Loading Layer:**

- `app/document_loader/` - Production-grade document ingestion
  - `PDFLoader` - Uses pdfplumber + PyMuPDF fallback
  - `DOCXLoader` - Uses python-docx with page break detection
  - `TXTLoader` - Plain text processing
  - `DocumentLoaderFactory` - Dispatch based on file extension
  - `models.py` - DocumentResult, PageResult, FileMetadata Pydantic models

**Legal Document Generation Layer:**

- `app/adjudication/routes.py` - Adjudication document generation
- `app/case_file_generator/routes.py` - Case file generation
- `app/case_file_generator/templates/` - HTML templates
- `app/utils/pdf_utils.py` - WeasyPrint integration
- `WeasyPrint` - PDF generation from HTML templates

**Backend Processing:**

- Flask blueprints for different domains
- SQLAlchemy models for document state
- Celery tasks for async PDF generation
- Google Sheets integration for audit trails

## Requirements for New Feature

### User Requirements

1. **Web Preview**: HTML-based document viewer in browser
2. **In-Browser Editing**: Real-time editing capabilities
3. **Document Management**: Save/load editing sessions
4. **Download Integration**: Export edited content as PDF/DOCX
5. **Legal Template Awareness**: Editing within legal document context

### Technical Requirements

1. **Multi-Format Support**: PDF, DOCX, TXT → HTML rendering
2. **Real-time Conversion**: Text → HTML → DOCX → PDF workflow
3. **Session Management**: Save/load editing states
4. **Template Integration**: HTML editor with legal template awareness
5. **Responsive Design**: Mobile-friendly web interface
6. **Audit Trail**: Track changes and versions

## Technical Approach

### Architecture Design

```
Browser (Frontend)
    ↓
Document Viewer Component (HTML/JS)
    ↓
REST API (Flask Backend)
    ↓
app/document_viewer/
    ├─ HTML Viewer (text → HTML conversion)
    ├─ HTML Editor (rich text editing)
    ├─ Save Manager (state persistence)
    └─ Download Generator (PDF/DOCX export)
    ↓
Existing Backend Services
    ↓
DocumentLoaderFactory → Text Extraction
    ↓
Legal Templates → WeasyPrint → PDF Generation
```

### Core Implementation Strategy

**1. HTML Viewer Layer**

- Convert extracted text to formatted HTML
- Implement page navigation and search
- Add responsive design for mobile
- Support for text highlighting and annotations

**2. HTML Editor Layer**

- Rich text editor with legal template awareness
- Form field overlays for legal documents
- Real-time preview updates
- Change tracking and versioning

**3. Integration Layer**

- Connect to existing DocumentLoaderFactory
- Maintain WeasyPrint unchanged for PDF generation
- Integrate with legal template system
- Add session management for editing states

## Implementation Plan

### Phase 1: Core Infrastructure (Weeks 1-2)

**Files to Create:**

```
app/document_viewer/
├── __init__.py
├── html_viewer.py          # HTML-based document preview
├── html_editor.py          # Rich text editor
├── save_manager.py         # Session persistence
└── __pycache__/            # Compiled Python files
```

**Phase 1 Tasks:**

1. **HTML Viewer Implementation**
   - Text-to-HTML conversion pipeline
   - Page navigation and search
   - Responsive design implementation
   - Format preservation (headers, lists, etc.)

2. **Editor Framework**
   - Base editor architecture
   - Legal template awareness
   - Real-time change detection
   - API integration with backend

3. **State Management**
   - Session storage (localStorage + backend)
   - Auto-save functionality
   - Version control basics
   - Conflict resolution

### Phase 2: Advanced Features (Weeks 2-3)

**Phase 2 Tasks:**

1. **Conversion Pipeline**
   - HTML → DOCX conversion
   - DOCX → editable content
   - PDF regeneration pipeline
   - Format preservation across conversions

2. **Editor Features**
   - Rich text formatting
   - Legal template field detection
   - Comment/note system
   - Export/import functionality

3. **API Integration**
   - RESTful API design
   - JWT authentication
   - Rate limiting
   - Error handling

### Phase 3: Legal Document Integration (Weeks 3-4)

**Phase 3 Tasks:**

1. **Template Integration**
   - Connect to existing legal templates
   - Form field recognition
   - Auto-population from case data
   - Validation rules for legal content

2. **Generation Pipeline**
   - Edited content → legal template merge
   - WeasyPrint PDF generation (unchanged)
   - Download endpoint integration
   - Audit trail generation

3. **Backend Enhancement**
   - Modify `app/adjudication/routes.py`
   - Modify `app/case_file_generator/routes.py`
   - Add editing-related endpoints
   - Update existing workflows

### Phase 4: Testing & Deployment (Weeks 4-6)

**Phase 4 Tasks:**

1. **Testing**
   - Unit tests for HTML conversion
   - Integration tests for editor
   - End-to-end tests for full workflow
   - Performance testing

2. **Deployment**
   - Docker containerization
   - CI/CD pipeline setup
   - Monitoring and logging
   - Security hardening

## Code Structure & Integration Points

### New Module: `app/document_viewer/`

**`__init__.py`**

```python
# Registration and initialization
from flask import Blueprint

document_viewer_bp = Blueprint("document_viewer", __name__, template_folder="templates")

from app.document_viewer import routes  # noqa: F401
```

**`html_viewer.py`**

```python
class HTMLViewer:
    """Convert extracted text to HTML for web preview"""
    
    def __init__(self, document_id: str):
        self.document_id = document_id
        self.document_loader = DocumentLoaderFactory
        
    def load_and_render(self, file_path: str) -> str:
        """Load document and convert to HTML"""
        doc_result = self.document_loader.load(file_path)
        return self._build_html(doc_result)
        
    def _build_html(self, doc_result: DocumentResult) -> str:
        """Convert DocumentResult to HTML"""
        # Text → formatted HTML with proper structure
        # Maintain formatting, headers, lists, etc.
```

**`html_editor.py`**

```python
class HTMLEditor:
    """Rich text editor with legal template awareness"""
    
    def __init__(self, template_context: dict):
        self.template_context = template_context
        self.form_fields = self._extract_form_fields()
        
    def edit_content(self, html_content: str, edits: dict) -> str:
        """Apply edits to HTML content"""
        # Handle text changes, form fields, comments
        return modified_html
        
    def _extract_form_fields(self) -> list:
        """Extract legal template form fields"""
        # Identify editable areas in templates
```

**`save_manager.py`**

```python
class EditSessionManager:
    """Manage editing sessions and state persistence"""
    
    def __init__(self, document_id: str):
        self.document_id = document_id
        
    def save_session(self, content: str, metadata: dict) -> str:
        """Save editing session"""
        # Store in localStorage + backend DB
        return session_id
        
    def load_session(self, session_id: str) -> tuple:
        """Load editing session"""
        # Retrieve content and metadata
```

### Modified Existing Files

**`app/adjudication/routes.py`**

- Add editing endpoints
- Integrate with HTML viewer/editor
- Update document regeneration workflow

**`app/case_file_generator/routes.py`**

- Add editing capabilities
- Integrate with preview system
- Update generation pipeline

## API Endpoints to Implement

### Editing Workflow Endpoints

```
GET /documents/<id>/view     # HTML viewer
GET /documents/<id>/editor   # HTML editor with template
POST /documents/<id>/save    # Save editing state
GET /documents/<id>/download # Download as PDF/DOCX
DELETE /documents/<id>/session # Clear editing session

# Backend processing endpoints
POST /api/documents/process   # Process uploaded document
GET /api/documents/<id>/history # Get edit history
POST /api/documents/<id>/export # Export with specific options
```

### Integration Endpoints

```
# Adjudication routes
GET /adjudication/<id>/edit-preview   # Preview with editing capability
POST /adjudication/<id>/save-edits    # Save edited content
GET /adjudication/<id>/regenerate     # Regenerate from edits

# Case file routes
GET /case-file/<id>/edit-preview      # Preview with editing capability
POST /case-file/<id>/save-edits       # Save edited content
GET /case-file/<id>/regenerate        # Regenerate from edits
```

## Testing Strategy

### Unit Tests

- **HTML Conversion**: Text → HTML → DOCX → PDF roundtrip
- **Editor Functionality**: Rich text editing, form fields
- **Session Management**: Save/load, conflict resolution
- **Template Integration**: Form field recognition

### Integration Tests

- **Full Workflow**: Upload → Edit → Save → Generate → Download
- **Multi-Format Support**: PDF, DOCX, TXT processing
- **Legal Template Validation**: Legal document requirements
- **API Integration**: All endpoints working together

### E2E Tests

- **User Journey**: Complete editing workflow
- **Cross-browser Compatibility**: Chrome, Firefox, Safari
- **Mobile Testing**: Responsive design
- **Performance**: Large document handling

## Performance Considerations

### Optimization Points

1. **Text Processing**: Efficient HTML conversion
   - Lazy loading of large documents
   - Incremental rendering
   - Memory management

2. **Real-time Updates**
   - Debounced editor updates
   - Selective re-rendering
   - WebSocket for live changes

3. **PDF Generation**
   - Maintain existing WeasyPrint pipeline
   - Optimize for edited content
   - Cache generated PDFs

### Scalability

- Horizontal scaling for concurrent users
- Database optimization for sessions
- CDN integration for static assets
- Load balancing for editing workload

## Security Considerations

### Access Control

- Document ownership verification
- Role-based access (FSO, Admin, Auditor)
- Session expiration and timeout
- CSRF protection for forms

### Data Protection

- Encryption for sensitive documents
- Audit logging for all edits
- Sanitization of user input
- Rate limiting to prevent abuse

## Deployment Configuration

### Docker Setup

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application code
COPY app/ ./app/

# Copy new document viewer
COPY app/document_viewer/ ./app/document_viewer/

EXPOSE 5000

CMD ["flask", "run", "--host=0.0.0.0"]
```

### Environment Variables

```bash
# Security
SECRET_KEY=your-secret-key
JWT_SECRET=your-jwt-secret

# Document Processing
MAX_DOC_SIZE=100MB
ALLOWED_EXTENSIONS=pdf,docx,txt

# Editing Session
SESSION_TIMEOUT=30m
MAX_EDIT_HISTORY=100

# Performance
WORKER_CONCURRENCY=4
PDF_CACHE_DURATION=24h

# Legal Documents
LEGAL_TEMPLATE_PATH=/app/case_file_generator/templates
MAX_LEGAL_PAGES=50
```

## Technical Debt Management

### Tools & Practices

- **Code Quality**: Black formatting, flake8 linting
- **Testing**: Comprehensive test coverage
- **Documentation**: API docs, user guides
- **Monitoring**: Error tracking, performance metrics

### Migration Strategy

1. **Start Small**: Basic HTML viewer functionality
2. **Expand Gradually**: Add editor features
3. **Integrate**: Connect to existing systems
4. **Optimize**: Performance and UX improvements

## Timeline (6 Weeks)

| Week | Milestone | Deliverables |
|------|-----------|--------------|
| 1-2  | Core Infrastructure | HTML viewer, editor framework, state management |
| 3    | Advanced Features   | HTML-to-DOCX conversion, rich editing |
| 4    | Legal Integration   | Template integration, PDF generation pipeline |
| 5    | Testing & Deployment | Test coverage, CI/CD, Docker setup |
| 6    | Launch & Documentation | Production deployment, user guides |

## Success Metrics

### Technical

- 95% test coverage
- < 100ms response time for viewer
- 99.9% uptime
- Zero breaking changes to existing API

### User Experience

- 80% reduction in download friction
- 90% task completion rate
- < 2s page load time
- Mobile-friendly interface

### Legal Compliance

- All legal documents generated correctly
- Audit trail maintained
- Version control accurate
- Access properly restricted

## Conclusion

This implementation provides a modern web-based document management system that significantly improves user experience while maintaining full backward compatibility with existing legal document generation workflows. The phased approach ensures risk mitigation and allows for iterative improvements based on user feedback.

The key success factors are:

1. Maintaining existing backend architecture
2. Adding web-based editing capabilities
3. Seamless integration with legal templates
4. Robust state management and auditing
5. Performance optimization for document processing
