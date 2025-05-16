# AI-Powered Subtitle Platform - Enhanced Backend

This is an enhanced backend API for an AI-powered subtitle generation platform built with FastAPI, PostgreSQL, and various AI services.

python scripts/init_admin.py --email super@admin.com --password admin --role super_admin

## Key Features

- **Comprehensive Role-Based Authentication**:
  - User, Admin, and Super Admin roles
  - Permission-based access control
  - Secure JWT authentication

- **Enhanced Admin Panel**:
  - Order management with manual processing/refund capabilities
  - System settings configuration
  - User management with role assignments
  - Comprehensive statistics dashboard
  - Activity logging for all admin actions
  - QA tools for subtitle verification

- **Improved User Experience**:
  - Streamlined signup/login flow with immediate token return
  - Clear role identification on authentication
  - Detailed error messages

- **Advanced Subtitle Generation**:
  - AssemblyAI for speech recognition
  - YAMNet for non-verbal sound detection
  - Multiple subtitle formats (SRT, VTT, ASS, TXT)
  - Accessibility features
  - Customizable subtitle settings

- **Robust Payment Processing**:
  - Stripe integration
  - Per-minute billing
  - Admin-initiated refunds

## Installation

1. Clone the repository
   ```bash
   git clone https://github.com/yourusername/ai-subtitle-platform.git
   cd ai-subtitle-platform
   ```

2. Create and activate a virtual environment
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies
   ```bash
   pip install -r requirements.txt
   ```

4. Set up environment variables
   ```bash
   cp .env.example .env
   # Edit .env with your API keys and settings
   ```

5. Create the PostgreSQL database
   ```bash
   createdb ai_subtitles
   ```

6. Create a super admin user
   ```bash
   python scripts/init_admin.py --email admin@example.com --password securepwd --role super_admin --first-name Admin
   ```

7. Run the application
   ```bash
   python run.py
   ```

8. Access the API documentation at http://127.0.0.1:8000/docs

## Role-Based Access Control

The system implements a three-tiered role system:

1. **User**: Regular users who can create orders and manage their own content
2. **Admin**: System administrators who can manage orders, settings, and view statistics
3. **Super Admin**: Highest level of access, can manage user roles and all system aspects

Each role has specific permissions:

- **User Permissions**:
  - Create and manage own orders
  - View and download own subtitle files

- **Admin Permissions**:
  - All user permissions
  - View and manage all orders
  - Access system settings
  - View statistics and logs
  - QA subtitle files
  - Process refunds

- **Super Admin Permissions**:
  - All admin permissions
  - Manage user roles
  - Create other admins
  - Unrestricted system access

## API Testing Flow

### Authentication Flow

1. **Register a New User**:
   - Endpoint: `POST /api/auth/register`
   - Provides: User details and immediate access token
   - Response includes user role

2. **Login**:
   - Endpoint: `POST /api/auth/login`
   - Provides: Access token and user role
   - Use this token for all subsequent requests

### User Operations

1. **Upload Videos**:
   - Endpoint: `POST /api/orders/videos/upload`
   - Authentication: User token
   - Returns: Video ID for order creation

2. **Create Order**:
   - Endpoint: `POST /api/orders/create`
   - Authentication: User token
   - Requires: Video IDs and subtitle configuration

3. **Process Payment**:
   - Endpoint: `POST /api/payments/create-checkout-session/{order_id}`
   - Authentication: User token
   - Initiates Stripe payment process

4. **Download Subtitles**:
   - Endpoint: `GET /api/subtitles/{subtitle_file_id}/download`
   - Authentication: User token
   - Requires: Completed order and subtitle files

### Admin Operations

1. **Manage Users**:
   - Endpoint: `GET /api/admin/users`
   - Authentication: Admin token
   - Provides: List of all users with order statistics

2. **Manage Orders**:
   - Endpoint: `GET /api/admin/orders`
   - Authentication: Admin token
   - Provides: Filterable list of all orders

3. **Update Order Status**:
   - Endpoint: `PUT /api/admin/orders/{order_id}`
   - Authentication: Admin token
   - Allows: Changing order status, payment status, adding notes

4. **Manage System Settings**:
   - Endpoint: `PUT /api/admin/settings/{key}`
   - Authentication: Admin token
   - Controls: Pricing, file size limits, etc.

5. **View Statistics**:
   - Endpoint: `GET /api/admin/stats`
   - Authentication: Admin token
   - Provides: Order counts, revenue, user statistics

6. **Reprocess Orders**:
   - Endpoint: `POST /api/admin/orders/{order_id}/reprocess`
   - Authentication: Admin token
   - Triggers: Re-generation of subtitles

7. **Process Refunds**:
   - Endpoint: `POST /api/admin/orders/{order_id}/refund`
   - Authentication: Admin token
   - Handles: Payment refunds with admin notes

8. **QA Subtitle Files**:
   - Endpoint: `PUT /api/admin/subtitle/{subtitle_id}/qa-status`
   - Authentication: Admin token
   - Allows: Marking files as approved/rejected with notes

### Super Admin Operations

1. **Manage User Roles**:
   - Endpoint: `PUT /api/admin/users/{user_id}/role`
   - Authentication: Super Admin token
   - Allows: Promoting users to admin or super admin

## Automated Testing

For comprehensive testing, we recommend creating a test script that:
1. Creates test users with different roles
2. Executes the complete flow for each role
3. Verifies correct access control
4. Cleans up test data

## Development

### Adding New Features

When adding new features, ensure you:
1. Update the role permissions in `app/core/security.py` if needed
2. Add appropriate activity logging
3. Implement proper error handling
4. Update the models to reflect new data structures
5. Consider RBAC implications

### Database Migrations

For database schema changes:
1. Create a migration script in `scripts/migrations/`
2. Test migration on a development database
3. Update the `create_tables` function in `app/core/database.py`

## System Architecture

```
Client → FastAPI → Authentication → RBAC → Business Logic → Database
                               ↑
                           Services
                               ↓
                         External APIs
                    (AssemblyAI, YAMNet, Stripe)
```

## License

MIT