Dunedeck Millers — Business Management System
A full-stack ERP-style business management system for a flour milling operation, built with Python and Flask. Designed to digitize and control the entire operational lifecycle of a milling factory — from raw maize intake to finished goods dispatch and financial reporting.

🏭 What It Does

Raw material intake — workers record incoming maize grain deliveries cage by cage, with automatic weight-to-bag conversion (90kg per bag)
Stock movement control — three-tier tracking: received stock, outstock (manager-authorized), and lost/damaged records
Processing records — tracks conversion of raw maize into finished products (1kg bags, 2kg bags, maize germ, animal feeds)
Dispatch management — finished goods leaving for delivery with driver details, vehicle registration, and itemized product lists
Sales invoicing — full invoice lifecycle with incremental payment tracking (unpaid → partial → paid)
Supplier management — purchase records for raw materials with payment tracking
Financial dashboard — real-time profit/loss with daily, weekly, and monthly charts


👥 Role-Based Access Control
RoleAccessWorkerClock in (warehouse WiFi only), create weight sheets, invoices, requisitions. Own records only.ManagerAll worker actions + authorize stock movements, dispatches, suppliers, all recordsDirectorRead-only executive dashboard — financials, inventory, attendance, audit trailsAdminUser management only — create accounts, reset passwords, block users

🔒 Security Features

IP-locked clock-in — warehouse WiFi only
Manager sign-off required for all outgoing stock
Full audit trail — every action logged with user and timestamp
Role isolation — workers cannot see other workers' data


🛠️ Tech Stack

Backend: Python, Flask
Database: SQLite
Frontend: HTML5, CSS3, JavaScript, Bootstrap
Other: Jinja2 templating, Chart.js


🚧 Status
In active development. Core modules functional. Upcoming:

 PDF export for invoices and dispatch notes
 Email notifications for manager approvals
 Mobile-responsive worker interface
 Cloud deployment (AWS/GCP)
