from dotenv import load_dotenv; load_dotenv()
from zoho_ticket_creation import create_zoho_ticket, DEPT_GENERAL

r = create_zoho_ticket('SK Test Phone number ', 'CS reply test.', DEPT_GENERAL, 'Medium', '0192585268')
print('Ticket #:', r.get('ticketNumber'), '| Status:', r.get('status'))
