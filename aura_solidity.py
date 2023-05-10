import os

from collections import deque

from solidity_parser import parser

from neo4j import GraphDatabase
import logging
from neo4j.exceptions import ServiceUnavailable


class App:

    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.parser = parser

    def close(self):
        # Don't forget to close the driver connection when you are finished with it
        self.driver.close()

    def execute_query(self, query, query_vals):
        with self.driver.session(database="neo4j") as session:
            result = session.execute_write(
                self._execute_query, query, query_vals
            )
        return result

    @staticmethod
    def _execute_query(tx, query, query_vals):
        result = tx.run(query, **query_vals)
        try:
            return result
        # Capture any errors along with the query and data for traceability
        except ServiceUnavailable as exception:
            logging.error("{query} raised an error: \n {exception}".format(
                query=query, exception=exception))
            raise

    @staticmethod
    def build_query_from_contracts(parsed_contracts, wallet_address):
        query = ""
        query_vals = {}

        main_contract = next(filter(lambda x: x.get("main_contract") == True, parsed_contracts))
        # create the node
        query += "MERGE (p1:Contract { contract_name: $contract_name, pragma_version: $pragma_version, wallet_address: $wallet_address }) \n"
        query_vals['contract_name'] = main_contract.get("contracts")
        query_vals['pragma_version'] = main_contract.get("pragma")
        query_vals['wallet_address'] = wallet_address


        # create the relationships
        alternate_contracts = list(filter(lambda x: x.get("main_contract") == False, parsed_contracts))

        # loop through main_contract imports
        for idx, import_contract in enumerate(main_contract.get("imports"), start=2):
            import_name = import_contract.split("/")[-1].split(".")[0]
            # check if import is in alternate_contracts
            import_contact_details = next(filter(lambda x: f'|{import_name}(' in x.get("contracts"), alternate_contracts))
            
            # create the node
            query += "MERGE (p" + str(idx) + ":Import {" + f"import_name: $import_name_{idx}, pragma_version: $pragma_version_{idx}, path: $path_{idx} " + "}) \n"
            query_vals[f'import_name_{idx}'] = import_name
            query_vals[f'pragma_version_{idx}'] = import_contact_details.get("pragma")
            query_vals[f'path_{idx}'] = import_contract

            # create the relationship
            query += "MERGE (p1)-[:IMPORTS]->(p" + str(idx) + ") \n"

        return query, query_vals


    @staticmethod
    def parse_contracts_from_ast(ast):
        relationships = []
        pragma = None
        imports = None
        contracts = None
        main_contract = False

        for node in ast.get("children"):
 
            match node_type := node.get("type"):
                case "PragmaDirective":
                    # Store if data is available
                    if pragma is not None:
                        relationships.append({
                            'pragma': pragma,
                            'imports': imports,
                            'contracts': contracts,
                            'main_contract': main_contract,
                        })

                    # Call reset for tracking vars
                    pragma = node.get("name") + node.get("value")
                    imports = []
                    contracts = '|'
                    main_contract = False

                case "ImportDirective":
                    # pragma should always be found before imports
                    assert(isinstance(imports, list))
                    imports.append(node.get("path"))

                case "ContractDefinition":
                    # pragma should always be found before contracts
                    assert(isinstance(contracts, str))
                    contracts += f'{node.get("name")}({node.get("kind")})|'

                    if node.get("kind") == "contract":
                        main_contract = True

                    # TODO - hashing to identify and inheritance
                    # hashable = node.get("baseContracts") + node.get("subNodes")

                case _:
                    print(f'Unknown node type: {node_type}')

        return relationships

    def drop_all(self):
        with self.driver.session(database="neo4j") as session:
            session.run("MATCH (n) DETACH DELETE n")
            print("All nodes and relationships have been deleted from the database.")

def bfs_dir(root_dir: str):
    queue = deque()
    queue.append(root_dir)
    max_queue = 10
    files = []

    while queue:
        # Pop a directory from the left of the queue
        current_dir = queue.popleft()

        try:
            # Loop through each file/directory in the current directory
            for name in os.listdir(current_dir):
                path = os.path.join(current_dir, name)

                # If path is a directory, add it to the queue
                if os.path.isdir(path):
                    queue.append(path)

                # If path is a file, print it
                elif os.path.isfile(path):
                    files.append(path)

                if len(queue) == max_queue:
                    break

        except PermissionError:
            # Skip directories that require permissions
            pass

    return files

def main():
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USERNAME")
    password = os.environ.get("NEO4J_PASSWORD")

    files = bfs_dir("./test")
    # print(files)

    app = App(uri, user, password)
    # clear
    app.drop_all()

    for file in files:
        try:
            with open(file) as f:
                solidity = f.read()

            wallet_address = file.split("_")[0]

            ast = parser.parse(solidity)

            relationships = app.parse_contracts_from_ast(ast)
            query, query_vals = app.build_query_from_contracts(relationships, wallet_address)

            app.execute_query(query, query_vals)

        except Exception as e:
            print(f'Error: {e}')

    app.close()

if __name__ == "__main__":
    main()


