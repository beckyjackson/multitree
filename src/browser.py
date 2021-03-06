import os
import sqlite3
import urllib.parse

from gizmos import hiccup, tree, search
from jinja2 import Template


# Look for list of database files
# For each db file, create a column in HTML page
# Only search first db for the term (in search bar) - for now, merge results later

# ?dbs=x,y,z&id=foo:bar


def get_rdfa(cur, prefixes, term_id):
	ontology_iri, ontology_title = tree.get_ontology(cur, prefixes)
    if term_id not in tree.top_levels:
        # Get a hierarchy under the entity type
        entity_type = tree.get_entity_type(cur, term_id)
        hierarchy, curies = tree.get_hierarchy(cur, term_id, entity_type, add_children=add_children)
    else:
        # Get the top-level for this entity type
        entity_type = term_id
        if term_id == "ontology":
            hierarchy = {term_id: {"parents": [], "children": []}}
            curies = set()
            if ontology_iri:
                curies.add(ontology_iri)
        else:
            if term_id == "owl:Individual":
                tls = ", ".join([f"'{x}'" for x in top_levels.keys()])
                cur.execute(
                    f"""SELECT DISTINCT subject FROM statements
                    WHERE subject NOT IN
                        (SELECT subject FROM statements
                         WHERE predicate = 'rdf:type'
                         AND object NOT IN ('owl:Individual', 'owl:NamedIndividual'))
                    AND subject IN
                        (SELECT subject FROM statements
                         WHERE predicate = 'rdf:type' AND object NOT IN ({tls}))"""
                )
            elif term_id == "rdfs:Datatype":
                cur.execute(
                    """SELECT DISTINCT subject FROM statements
                    WHERE predicate = 'rdf:type' AND object = 'rdfs:Datatype'"""
                )
            else:
                pred = "rdfs:subPropertyOf"
                if term_id == "owl:Class":
                    pred = "rdfs:subClassOf"
                # Select all classes without parents and set them as children of owl:Thing
                cur.execute(
                    f"""SELECT DISTINCT subject FROM statements 
                    WHERE subject NOT IN 
                        (SELECT subject FROM statements
                         WHERE predicate = '{pred}'
                         AND object IS NOT 'owl:Thing')
                    AND subject IN 
                        (SELECT subject FROM statements 
                         WHERE predicate = 'rdf:type'
                         AND object = '{term_id}' AND subject NOT LIKE '_:%'
                         AND subject NOT IN ('owl:Thing', 'rdf:type'));"""
                )
            children = [row["subject"] for row in cur.fetchall()]
            hierarchy = {term_id: {"parents": [], "children": children}}
            curies = {term_id}
            for c in children:
                hierarchy[c] = {"parents": [term_id], "children": []}
                curies.add(c)

    # Add all of the other compact URIs in the stanza to the set of compact URIs:
    stanza.sort(key=lambda x: x["predicate"])
    for row in stanza:
        curies.add(row.get("subject"))
        curies.add(row.get("predicate"))
        curies.add(row.get("object"))
    curies.discard("")
    curies.discard(None)

    # Get all the prefixes that are referred to by the compact URIs:
    ps = set()
    for curie in curies:
        if not isinstance(curie, str) or len(curie) == 0 or curie[0] in ("_", "<"):
            continue
        prefix, local = curie.split(":")
        ps.add(prefix)

    # Get all of the rdfs:labels corresponding to all of the compact URIs, in the form of a map
    # from compact URIs to labels:
    labels = {}
    ids = "', '".join(curies)
    cur.execute(
        f"""SELECT subject, value
      FROM statements
      WHERE stanza IN ('{ids}')
        AND predicate = 'rdfs:label'
        AND value IS NOT NULL"""
    )
    for row in cur:
        labels[row["subject"]] = row["value"]
    for t, o_label in tree.top_levels.items():
        labels[t] = o_label
    if ontology_iri and ontology_title:
        labels[ontology_iri] = ontology_title

    obsolete = []
    cur.execute(
        f"""SELECT DISTINCT subject
            FROM statements
            WHERE stanza in ('{ids}')
              AND predicate='owl:deprecated'
              AND value='true'"""
    )
    for row in cur:
        obsolete.append(row["subject"])

    # If the compact URIs in the labels map are also in the tree, then add the label info to the
    # corresponding node in the tree:
    for key in hierarchy.keys():
        if key in labels:
            hierarchy[key]["label"] = labels[key]

    # Initialise a map with one entry for the tree and one for all of the labels corresponding to
    # all of the compact URIs in the stanza:
    data = {"labels": labels, "obsolete": obsolete, treename: hierarchy, "iri": ontology_iri}

    # Determine the label to use for the given term id when generating RDFa (the term might have
    # multiple labels, in which case we will just choose one and show it everywhere). This defaults
    # to the term id itself, unless there is a label for the term in the stanza corresponding to the
    # label for that term in the labels map:
    if term_id in labels:
        selected_label = labels[term_id]
    else:
        selected_label = term_id
    label = term_id
    for row in stanza:
        predicate = row["predicate"]
        value = row["value"]
        if predicate == "rdfs:label" and value == selected_label:
            label = value
            break

    subject = None
    si = None
    subject_label = None
    if term_id == "ontology" and ontology_iri:
        cur.execute(
            f"""SELECT * FROM statements
            WHERE subject = '{ontology_iri}'"""
        )
        stanza = cur.fetchall()
        subject = ontology_iri
        subject_label = data["labels"].get(ontology_iri, ontology_iri)
        si = tree.curie2iri(prefixes, subject)
    elif term_id != "ontology":
        subject = term_id
        si = tree.curie2iri(prefixes, subject)
        subject_label = label

    return tree.term2tree(data, treename, term_id, entity_type, href=href)


def get_tree_html(db, href, term):
	with sqlite3.connect(f"build/{db}.db") as conn:
		conn.row_factory = tree.dict_factory
		cur = conn.cursor()

		# Get prefixes
		cur.execute("SELECT * FROM prefix ORDER BY length(base) DESC")
    	all_prefixes = [(x["prefix"], x["base"]) for x in cur.fetchall()]

    	predicate_ids = tree.get_sorted_predicates(cur)

    	if term == "owl:Class":
    		stanza = []
    	else:
    		cur.execute(f"SELECT * FROM statements WHERE stanza = '{term}'")
        	stanza = cur.fetchall()
    	
        # get tree rdfa hiccup vector
        hiccup = get_rdfa(cur, prefixes, term_id)
        return hiccup.render(prefixes, hiccup, href=href)


def main():
	if "QUERY_STRING" in os.environ:
		args = dict(urllib.parse.parse_qsl(os.environ["QUERY_STRING"]))
	else:
		sys.exit(1)

	if "dbs" not in args:
		sys.exit(1)

	dbs = args["dbs"].split(",")
	first_db = dbs.pop(0)

	if "format" in args and args["format"] == "json":
		# TODO - maybe we can search both & merge results?
		json = search.search(f"build/{first_db}.db", args["text"])
		print("Content-Type: application/json")
		print("")
		print(json)
		return

	term = "owl:Class"
	if "id" in args:
		term = args["id"]

	href = f"?dbs={args['dbs']}&id={curie}"

	first_html = get_tree_html(first_db, href, term)

	trees = []
	for db in dbs:
		trees.append(get_tree_html(db, href, term))

	# Load Jinja template with CSS & JS and left & right trees
	with open("src/index.html.jinja2", "r") as f:
		t = Template(f.read())

	html = t.render(first=first_html, trees=trees, title="test")

	# Return with CGI headers
	print("Content-Type: text/html")
	print("")
	print(html)
