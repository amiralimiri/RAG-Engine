## ساخت نودها

// نود کارگردان
CREATE (nolan:Person {name: "Christopher Nolan", born: 1970})

// نود بازیگرها
CREATE (leo:Person {name: "Leonardo DiCaprio", born: 1974})
CREATE (matthew:Person {name: "Matthew McConaughey", born: 1969})

// نود فیلم‌ها
CREATE (inception:Movie {title: "Inception", released: 2010})
CREATE (interstellar:Movie {title: "Interstellar", released: 2014})

// نود ژانرها
CREATE (scifi:Genre {name: "Science Fiction"})
CREATE (action:Genre {name: "Action"})

## ساخت روابط

// کارگردان
MATCH (p:Person {name: "Christopher Nolan"}), (m:Movie {title: "Inception"})
CREATE (p)-[:DIRECTED]->(m)

MATCH (p:Person {name: "Christopher Nolan"}), (m:Movie {title: "Interstellar"})
CREATE (p)-[:DIRECTED]->(m)

// بازیگرها
MATCH (p:Person {name: "Leonardo DiCaprio"}), (m:Movie {title: "Inception"})
CREATE (p)-[:ACTED_IN]->(m)

MATCH (p:Person {name: "Matthew McConaughey"}), (m:Movie {title: "Interstellar"})
CREATE (p)-[:ACTED_IN]->(m)

// ژانر
MATCH (m:Movie {title: "Inception"}), (g:Genre {name: "Science Fiction"})
CREATE (m)-[:HAS_GENRE]->(g)

## کوئری‌گیری (Retrieval دستی)

// چه کسی در فیلم‌های Nolan بازی کرده؟
MATCH (director:Person {name: "Christopher Nolan"})-[:DIRECTED]->(movie:Movie)<-[:ACTED_IN]-(actor:Person)
RETURN director.name, movie.title, actor.name

// فیلم‌های ۲۰۱۰ که Nolan کارگردانی کرده و چه کسی بازی کرده
MATCH (d:Person {name: "Christopher Nolan"})-[:DIRECTED]->(m:Movie {released: 2010})<-[:ACTED_IN]-(a:Person)
RETURN m.title, a.name
