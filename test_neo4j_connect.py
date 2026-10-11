from neo4j import GraphDatabase
uri = 'neo4j+s://21090cf9.databases.neo4j.io'
user = '21090cf9'
pwd = 'b7fH7k4Z9cwhOCc5BPTu4yZoHR2eS2Z'
try:
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    driver.verify_connectivity()
    print('CONNECTED')
    driver.close()
except Exception as e:
    print(f'FAILED: {type(e).__name__}: {e}')