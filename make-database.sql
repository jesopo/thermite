-- 16  is nickname length
-- 50  is channel length
-- 260 is reason length

BEGIN;

CREATE TABLE pipe (
    id      SERIAL PRIMARY KEY,
    source  VARCHAR(50)   NOT NULL,
    target  VARCHAR(50)   NOT NULL,
    reason  VARCHAR(260)  NOT NULL,
    ts      TIMESTAMP     NOT NULL
);
