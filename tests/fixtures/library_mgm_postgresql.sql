-- PostgreSQL-compatible verification fixture adapted from library_mgm_schema.ddl.
-- It retains the sample's author, publisher, and book relationships while using
-- only the documented supported DDL subset.
CREATE TABLE authors (
    author_id integer PRIMARY KEY,
    first_name varchar(100) NOT NULL,
    last_name varchar(100) NOT NULL,
    nationality varchar(100)
);

CREATE TABLE publishers (
    publisher_id integer PRIMARY KEY,
    name varchar(255) NOT NULL,
    city varchar(100),
    contact_email varchar(255)
);

CREATE TABLE books (
    book_id integer PRIMARY KEY,
    title varchar(255) NOT NULL,
    author_id integer NOT NULL REFERENCES authors(author_id),
    publisher_id integer NOT NULL REFERENCES publishers(publisher_id),
    isbn varchar(20) UNIQUE NOT NULL,
    publication_date date,
    number_of_pages integer CHECK (number_of_pages >= 1)
);
