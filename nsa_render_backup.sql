--
-- PostgreSQL database dump
--

\restrict NItvkvaCRAcLFQD9Rv2d1PQTEjDnY8vTaMqL1Df2RWa1k3M8V7QnE5SqhqRbKf3

-- Dumped from database version 18.4 (Debian 18.4-1.pgdg12+1)
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- Name: pg_stat_statements; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA public;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: adjudications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.adjudications (
    id integer NOT NULL,
    case_number character varying(100) NOT NULL,
    food_safety_officer character varying(100) NOT NULL,
    non_license character varying(10),
    pre_authorization character varying(10),
    complaint_lodged character varying(10),
    ce_license_no character varying(100),
    ce_trade_name character varying(200),
    ce_proprietor character varying(200),
    ce_address text,
    ce_status character varying(100),
    fbo_owner character varying(200) NOT NULL,
    fbo_name character varying(200) NOT NULL,
    fbo_address text NOT NULL,
    fssai_license character varying(100) NOT NULL,
    concerned_food character varying(200),
    problem text,
    "First_inspection_date" timestamp without time zone NOT NULL,
    compliance_deadline timestamp without time zone NOT NULL,
    "Complaint_date" timestamp without time zone,
    inspection_date timestamp without time zone NOT NULL,
    authorization_date timestamp without time zone,
    clean_premise character varying(10),
    refrigerator_clean character varying(10),
    proper_attire character varying(10),
    proper_covered_utensil character varying(10),
    date_tag character varying(10),
    veg_nonveg_separation character varying(10),
    food_segregation character varying(10),
    license_display character varying(10),
    artificial_colour character varying(10),
    "Expired_item" character varying(10),
    "Pest_report" character varying(10),
    "Water_report" character varying(10),
    section_55 character varying(10),
    section_56 character varying(10),
    section_58 character varying(10),
    section_63 character varying(10),
    section_64 character varying(10),
    created_at timestamp without time zone,
    synced_at timestamp without time zone
);


--
-- Name: adjudications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.adjudications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: adjudications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.adjudications_id_seq OWNED BY public.adjudications.id;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: app_secrets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_secrets (
    name character varying(64) NOT NULL,
    value text NOT NULL
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id integer NOT NULL,
    entity_type character varying NOT NULL,
    entity_id character varying NOT NULL,
    action character varying NOT NULL,
    actor character varying NOT NULL,
    "timestamp" timestamp without time zone NOT NULL,
    prev_hash character varying,
    curr_hash character varying,
    details_json text
);


--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id;


--
-- Name: bill_sample; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bill_sample (
    bill_id integer NOT NULL,
    sample_id integer NOT NULL
);


--
-- Name: bills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bills (
    id integer NOT NULL,
    "Name" character varying(100) NOT NULL,
    "EMP_ID" character varying(50) NOT NULL,
    "Designation" character varying(100) NOT NULL,
    "Enf_samp_No" integer NOT NULL,
    "Surv_samp_No" integer NOT NULL,
    enforcement_price numeric(10,2) NOT NULL,
    surveillance_price numeric(10,2) NOT NULL,
    "Total_bill" double precision NOT NULL,
    "No_of_enfbills" integer NOT NULL,
    "No_of_survbills" integer NOT NULL,
    "TR_Value" character varying(100) NOT NULL,
    "TR_date" timestamp without time zone NOT NULL,
    "Submission_date" timestamp without time zone NOT NULL,
    start_date timestamp without time zone,
    end_date timestamp without time zone,
    created_at timestamp without time zone,
    synced_at timestamp without time zone,
    pdf_task_id character varying(100),
    pdf_generated_at timestamp without time zone
);


--
-- Name: bills_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bills_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bills_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bills_id_seq OWNED BY public.bills.id;


--
-- Name: case_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.case_files (
    id integer NOT NULL,
    case_number character varying(100) NOT NULL,
    food_safety_officer_name character varying(100) NOT NULL,
    authorization_date timestamp without time zone NOT NULL,
    inspection_date timestamp without time zone NOT NULL,
    inspection_time character varying(100) NOT NULL,
    sample_id integer,
    manufacturer_fssai character varying(50) NOT NULL,
    manufacturer_name character varying(200) NOT NULL,
    manufacturer_fbo_name character varying(200) NOT NULL,
    manufacturer_address text NOT NULL,
    retailer_fssai character varying(50) NOT NULL,
    retailer_name character varying(200) NOT NULL,
    retailer_fbo_name character varying(200) NOT NULL,
    retailer_address text NOT NULL,
    product_name character varying(200) NOT NULL,
    batch_no character varying(100) NOT NULL,
    sample_quantity character varying(100) NOT NULL,
    packet_count integer NOT NULL,
    mfg_date timestamp without time zone NOT NULL,
    expiry_date timestamp without time zone NOT NULL,
    other_food_articles character varying(500),
    total_cost character varying(50),
    cost_in_words character varying(200),
    sample_code character varying(100) NOT NULL,
    sample_submission_date timestamp without time zone NOT NULL,
    "Lab_Registration_No" character varying(100) NOT NULL,
    do_receipt_date timestamp without time zone NOT NULL,
    is_misbranded boolean,
    is_substandard boolean,
    analyst_report_no character varying(100) NOT NULL,
    analyst_report_date timestamp without time zone NOT NULL,
    directive_letter_no character varying(100) NOT NULL,
    directive_letter_date timestamp without time zone NOT NULL,
    retailer_report_receive_date timestamp without time zone NOT NULL,
    manufacturer_report_receive_date timestamp without time zone NOT NULL,
    applicable_regulation character varying(200),
    applicable_clause character varying(200),
    sample_name character varying(200),
    applicable_sections character varying(50),
    created_at timestamp without time zone,
    synced_at timestamp without time zone,
    pdf_task_id character varying(100),
    pdf_generated_at timestamp without time zone
);


--
-- Name: case_files_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.case_files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: case_files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.case_files_id_seq OWNED BY public.case_files.id;


--
-- Name: code_sequence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.code_sequence (
    key character varying(50) NOT NULL,
    last_value integer NOT NULL
);


--
-- Name: fbo_issue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fbo_issue (
    id integer NOT NULL,
    fbo_id character varying NOT NULL,
    manufacturer_fbo_id character varying,
    fbo_name character varying NOT NULL,
    source_type character varying NOT NULL,
    state character varying NOT NULL,
    fso_name character varying NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    detail_json text,
    reg_lat double precision,
    reg_lng double precision,
    geocoded_at timestamp without time zone,
    CONSTRAINT ck_sample_not_dismissed CHECK ((NOT (((source_type)::text = 'sample'::text) AND ((state)::text = 'dismissed'::text)))),
    CONSTRAINT ck_sample_or_null_mfg CHECK ((((source_type)::text = 'sample'::text) OR (manufacturer_fbo_id IS NULL))),
    CONSTRAINT ck_source_type CHECK (((source_type)::text = ANY ((ARRAY['inspection'::character varying, 'sample'::character varying])::text[]))),
    CONSTRAINT ck_state CHECK (((state)::text = ANY ((ARRAY['open'::character varying, 'permission_pending'::character varying, 'permission_granted'::character varying, 'closed'::character varying, 'dismissed'::character varying])::text[])))
);


--
-- Name: fbo_issue_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fbo_issue_audit (
    id integer NOT NULL,
    issue_id integer NOT NULL,
    from_state character varying,
    to_state character varying NOT NULL,
    asserted_by character varying NOT NULL,
    asserted_at timestamp without time zone NOT NULL,
    note text
);


--
-- Name: fbo_issue_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fbo_issue_audit_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fbo_issue_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fbo_issue_audit_id_seq OWNED BY public.fbo_issue_audit.id;


--
-- Name: fbo_issue_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fbo_issue_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fbo_issue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fbo_issue_id_seq OWNED BY public.fbo_issue.id;


--
-- Name: fso; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fso (
    fso_name character varying(100) NOT NULL,
    created_at timestamp without time zone
);


--
-- Name: inspection; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inspection (
    id integer NOT NULL,
    inspection_code character varying(50) NOT NULL,
    fso_name character varying(100) NOT NULL,
    fssai_license character varying(50),
    ce_license_no character varying(100),
    fbo_name character varying(200),
    fbo_address text,
    concerned_food character varying(200),
    problem text,
    inspection_date timestamp without time zone NOT NULL,
    compliance_deadline timestamp without time zone NOT NULL,
    is_dismissed boolean,
    dismissed_by character varying(100),
    dismissed_at timestamp without time zone,
    adjudication_id integer,
    created_at timestamp without time zone,
    synced_at timestamp without time zone
);


--
-- Name: inspection_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.inspection_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: inspection_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.inspection_id_seq OWNED BY public.inspection.id;


--
-- Name: inspection_photos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inspection_photos (
    id integer NOT NULL,
    adjudication_id integer NOT NULL,
    file_url character varying(500) NOT NULL,
    caption character varying(200),
    uploaded_at timestamp without time zone
);


--
-- Name: inspection_photos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.inspection_photos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: inspection_photos_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.inspection_photos_id_seq OWNED BY public.inspection_photos.id;


--
-- Name: photo_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.photo_evidence (
    image_id character varying NOT NULL,
    case_id integer,
    inspection_id integer,
    filepath character varying NOT NULL,
    raw_lat double precision NOT NULL,
    raw_lng double precision NOT NULL,
    accuracy double precision NOT NULL,
    captured_at timestamp without time zone NOT NULL,
    uploaded_at timestamp without time zone NOT NULL,
    locality character varying,
    ip_region character varying,
    ip_match boolean,
    distance_to_fbo_m double precision,
    verification_status character varying,
    stamped boolean
);


--
-- Name: record_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_audit (
    id integer NOT NULL,
    user_id integer,
    action character varying(20) NOT NULL,
    record_type character varying(50) NOT NULL,
    record_id character varying(50) NOT NULL,
    changes text,
    "timestamp" timestamp without time zone NOT NULL,
    ip_address character varying(45),
    user_agent character varying(500)
);


--
-- Name: record_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.record_audit_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: record_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.record_audit_id_seq OWNED BY public.record_audit.id;


--
-- Name: sample; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sample (
    id integer NOT NULL,
    sample_code character varying(50) NOT NULL,
    sample_name character varying(200) NOT NULL,
    sample_type character varying(100) NOT NULL,
    fso_name character varying(100) NOT NULL,
    collection_date timestamp without time zone NOT NULL,
    submission_date timestamp without time zone,
    retailer_fssai character varying(50),
    retailer_name character varying(200),
    price character varying(50),
    billed boolean,
    created_at timestamp without time zone,
    synced_at timestamp without time zone
);


--
-- Name: sample_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sample_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sample_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sample_id_seq OWNED BY public.sample.id;


--
-- Name: user; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."user" (
    id integer NOT NULL,
    username character varying(80) NOT NULL,
    password_hash character varying(256) NOT NULL,
    created_at timestamp without time zone,
    is_admin boolean DEFAULT false NOT NULL
);


--
-- Name: user_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_id_seq OWNED BY public."user".id;


--
-- Name: adjudications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adjudications ALTER COLUMN id SET DEFAULT nextval('public.adjudications_id_seq'::regclass);


--
-- Name: audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);


--
-- Name: bills id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bills ALTER COLUMN id SET DEFAULT nextval('public.bills_id_seq'::regclass);


--
-- Name: case_files id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.case_files ALTER COLUMN id SET DEFAULT nextval('public.case_files_id_seq'::regclass);


--
-- Name: fbo_issue id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fbo_issue ALTER COLUMN id SET DEFAULT nextval('public.fbo_issue_id_seq'::regclass);


--
-- Name: fbo_issue_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fbo_issue_audit ALTER COLUMN id SET DEFAULT nextval('public.fbo_issue_audit_id_seq'::regclass);


--
-- Name: inspection id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspection ALTER COLUMN id SET DEFAULT nextval('public.inspection_id_seq'::regclass);


--
-- Name: inspection_photos id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspection_photos ALTER COLUMN id SET DEFAULT nextval('public.inspection_photos_id_seq'::regclass);


--
-- Name: record_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_audit ALTER COLUMN id SET DEFAULT nextval('public.record_audit_id_seq'::regclass);


--
-- Name: sample id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sample ALTER COLUMN id SET DEFAULT nextval('public.sample_id_seq'::regclass);


--
-- Name: user id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."user" ALTER COLUMN id SET DEFAULT nextval('public.user_id_seq'::regclass);


--
-- Data for Name: adjudications; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.adjudications (id, case_number, food_safety_officer, non_license, pre_authorization, complaint_lodged, ce_license_no, ce_trade_name, ce_proprietor, ce_address, ce_status, fbo_owner, fbo_name, fbo_address, fssai_license, concerned_food, problem, "First_inspection_date", compliance_deadline, "Complaint_date", inspection_date, authorization_date, clean_premise, refrigerator_clean, proper_attire, proper_covered_utensil, date_tag, veg_nonveg_separation, food_segregation, license_display, artificial_colour, "Expired_item", "Pest_report", "Water_report", section_55, section_56, section_58, section_63, section_64, created_at, synced_at) FROM stdin;
\.


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.alembic_version (version_num) FROM stdin;
add_is_admin_to_user
\.


--
-- Data for Name: app_secrets; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.app_secrets (name, value) FROM stdin;
secret_key	efc405f864f221b945757e05e4059a7b89f365466a0eca2b171729b2710ad444
\.


--
-- Data for Name: audit_log; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.audit_log (id, entity_type, entity_id, action, actor, "timestamp", prev_hash, curr_hash, details_json) FROM stdin;
\.


--
-- Data for Name: bill_sample; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.bill_sample (bill_id, sample_id) FROM stdin;
\.


--
-- Data for Name: bills; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.bills (id, "Name", "EMP_ID", "Designation", "Enf_samp_No", "Surv_samp_No", enforcement_price, surveillance_price, "Total_bill", "No_of_enfbills", "No_of_survbills", "TR_Value", "TR_date", "Submission_date", start_date, end_date, created_at, synced_at, pdf_task_id, pdf_generated_at) FROM stdin;
\.


--
-- Data for Name: case_files; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.case_files (id, case_number, food_safety_officer_name, authorization_date, inspection_date, inspection_time, sample_id, manufacturer_fssai, manufacturer_name, manufacturer_fbo_name, manufacturer_address, retailer_fssai, retailer_name, retailer_fbo_name, retailer_address, product_name, batch_no, sample_quantity, packet_count, mfg_date, expiry_date, other_food_articles, total_cost, cost_in_words, sample_code, sample_submission_date, "Lab_Registration_No", do_receipt_date, is_misbranded, is_substandard, analyst_report_no, analyst_report_date, directive_letter_no, directive_letter_date, retailer_report_receive_date, manufacturer_report_receive_date, applicable_regulation, applicable_clause, sample_name, applicable_sections, created_at, synced_at, pdf_task_id, pdf_generated_at) FROM stdin;
\.


--
-- Data for Name: code_sequence; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.code_sequence (key, last_value) FROM stdin;
\.


--
-- Data for Name: fbo_issue; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fbo_issue (id, fbo_id, manufacturer_fbo_id, fbo_name, source_type, state, fso_name, created_at, updated_at, detail_json, reg_lat, reg_lng, geocoded_at) FROM stdin;
\.


--
-- Data for Name: fbo_issue_audit; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fbo_issue_audit (id, issue_id, from_state, to_state, asserted_by, asserted_at, note) FROM stdin;
\.


--
-- Data for Name: fso; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fso (fso_name, created_at) FROM stdin;
Anwesha Paul	2026-07-26 05:26:22.842542
Kankana Srimany	2026-07-26 05:26:22.842547
Saikat Sarkar	2026-07-26 05:26:22.842548
Anik Koley	2026-07-26 05:26:22.842549
Ramiza Obaidi	2026-07-26 05:26:22.84255
Ranjan Dutta	2026-07-26 05:26:22.842551
Shamim Hossain	2026-07-26 05:26:22.842552
Tania Chakraborty	2026-07-26 05:26:22.842553
Jeannie Barla	2026-07-26 05:26:22.842554
Siddartha Mishra	2026-07-26 05:26:22.842555
Soifa Khatoon	2026-07-26 05:26:22.842555
Paresh Sharma	2026-07-26 05:26:22.842556
Amrita Mukherjee	2026-07-26 05:26:22.842557
Kingshuk Patra	2026-07-26 05:26:22.842558
Suman Saha	2026-07-26 05:26:22.842559
Arindam Paul	2026-07-26 05:26:22.84256
Pradip Malik	2026-07-26 05:26:22.84256
Jyotishman Soren	2026-07-26 05:26:22.842561
Rifat Islam	2026-07-26 05:26:22.842562
Md Sadruddin	2026-07-26 05:26:22.842563
Indranil Karmakar	2026-07-26 05:26:22.842564
\.


--
-- Data for Name: inspection; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.inspection (id, inspection_code, fso_name, fssai_license, ce_license_no, fbo_name, fbo_address, concerned_food, problem, inspection_date, compliance_deadline, is_dismissed, dismissed_by, dismissed_at, adjudication_id, created_at, synced_at) FROM stdin;
\.


--
-- Data for Name: inspection_photos; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.inspection_photos (id, adjudication_id, file_url, caption, uploaded_at) FROM stdin;
\.


--
-- Data for Name: photo_evidence; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.photo_evidence (image_id, case_id, inspection_id, filepath, raw_lat, raw_lng, accuracy, captured_at, uploaded_at, locality, ip_region, ip_match, distance_to_fbo_m, verification_status, stamped) FROM stdin;
\.


--
-- Data for Name: record_audit; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.record_audit (id, user_id, action, record_type, record_id, changes, "timestamp", ip_address, user_agent) FROM stdin;
1	1	login_success	auth	1	\N	2026-08-02 07:32:17.721297	10.26.103.129	Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) Gecko/20100101 Firefox/153.0
2	1	login_success	auth	1	\N	2026-08-03 05:48:36.091068	10.26.103.129	Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0
3	1	login_success	auth	1	\N	2026-08-04 05:31:36.746573	10.26.192.130	Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36
4	1	login_success	auth	1	\N	2026-08-04 06:50:23.761398	10.27.111.131	Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/30.0 Chrome/143.0.0.0 Mobile Safari/537.36
5	1	login_success	auth	1	\N	2026-08-06 05:54:02.591971	10.26.103.129	Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0
6	1	login_success	auth	1	\N	2026-08-06 14:34:25.316904	10.26.192.130	Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) Gecko/20100101 Firefox/153.0
7	\N	login_failed	auth	anonymous	\N	2026-08-06 14:45:38.677193	10.26.103.129	Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) Gecko/20100101 Firefox/153.0
8	\N	login_failed	auth	anonymous	\N	2026-08-06 14:45:49.148706	10.26.103.129	Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) Gecko/20100101 Firefox/153.0
9	\N	login_failed	auth	anonymous	\N	2026-08-06 14:46:02.685398	10.26.103.129	Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) Gecko/20100101 Firefox/153.0
10	1	login_success	auth	1	\N	2026-08-06 14:56:31.146147	10.26.103.129	Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) Gecko/20100101 Firefox/153.0
11	\N	login_failed	auth	anonymous	\N	2026-08-06 15:03:30.737589	10.27.111.131	curl/8.21.0
12	1	login_success	auth	1	\N	2026-08-21 12:13:24.930249	10.24.6.111	Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36
\.


--
-- Data for Name: sample; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.sample (id, sample_code, sample_name, sample_type, fso_name, collection_date, submission_date, retailer_fssai, retailer_name, price, billed, created_at, synced_at) FROM stdin;
\.


--
-- Data for Name: user; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public."user" (id, username, password_hash, created_at, is_admin) FROM stdin;
1	admin	scrypt:32768:8:1$c9kI3wdQWXzEIemC$d26e7bffc5359b44d77224de7f0a14f40f363a7ab8a503eaa9396940c78836e2d2b2aea9d3f134b57c8ed91dabac923687273d8b209d8b745f2af7e1146ca040	2026-08-02 04:26:57.751404	t
\.


--
-- Name: adjudications_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.adjudications_id_seq', 1, false);


--
-- Name: audit_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.audit_log_id_seq', 1, false);


--
-- Name: bills_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.bills_id_seq', 1, false);


--
-- Name: case_files_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.case_files_id_seq', 1, false);


--
-- Name: fbo_issue_audit_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fbo_issue_audit_id_seq', 1, false);


--
-- Name: fbo_issue_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fbo_issue_id_seq', 1, false);


--
-- Name: inspection_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.inspection_id_seq', 1, false);


--
-- Name: inspection_photos_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.inspection_photos_id_seq', 1, false);


--
-- Name: record_audit_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.record_audit_id_seq', 12, true);


--
-- Name: sample_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.sample_id_seq', 1, false);


--
-- Name: user_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_id_seq', 1, true);


--
-- Name: adjudications adjudications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adjudications
    ADD CONSTRAINT adjudications_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: app_secrets app_secrets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_secrets
    ADD CONSTRAINT app_secrets_pkey PRIMARY KEY (name);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: bill_sample bill_sample_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bill_sample
    ADD CONSTRAINT bill_sample_pkey PRIMARY KEY (bill_id, sample_id);


--
-- Name: bills bills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bills
    ADD CONSTRAINT bills_pkey PRIMARY KEY (id);


--
-- Name: case_files case_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.case_files
    ADD CONSTRAINT case_files_pkey PRIMARY KEY (id);


--
-- Name: code_sequence code_sequence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.code_sequence
    ADD CONSTRAINT code_sequence_pkey PRIMARY KEY (key);


--
-- Name: fbo_issue_audit fbo_issue_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fbo_issue_audit
    ADD CONSTRAINT fbo_issue_audit_pkey PRIMARY KEY (id);


--
-- Name: fbo_issue fbo_issue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fbo_issue
    ADD CONSTRAINT fbo_issue_pkey PRIMARY KEY (id);


--
-- Name: fso fso_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fso
    ADD CONSTRAINT fso_pkey PRIMARY KEY (fso_name);


--
-- Name: inspection inspection_inspection_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspection
    ADD CONSTRAINT inspection_inspection_code_key UNIQUE (inspection_code);


--
-- Name: inspection_photos inspection_photos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspection_photos
    ADD CONSTRAINT inspection_photos_pkey PRIMARY KEY (id);


--
-- Name: inspection inspection_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspection
    ADD CONSTRAINT inspection_pkey PRIMARY KEY (id);


--
-- Name: photo_evidence photo_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.photo_evidence
    ADD CONSTRAINT photo_evidence_pkey PRIMARY KEY (image_id);


--
-- Name: record_audit record_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_audit
    ADD CONSTRAINT record_audit_pkey PRIMARY KEY (id);


--
-- Name: sample sample_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sample
    ADD CONSTRAINT sample_pkey PRIMARY KEY (id);


--
-- Name: sample sample_sample_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sample
    ADD CONSTRAINT sample_sample_code_key UNIQUE (sample_code);


--
-- Name: user user_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."user"
    ADD CONSTRAINT user_pkey PRIMARY KEY (id);


--
-- Name: idx_fbo_issue_audit_issue_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fbo_issue_audit_issue_id ON public.fbo_issue_audit USING btree (issue_id);


--
-- Name: idx_fbo_issue_fbo_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fbo_issue_fbo_id ON public.fbo_issue USING btree (fbo_id);


--
-- Name: idx_fbo_issue_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fbo_issue_state ON public.fbo_issue USING btree (state);


--
-- Name: idx_fso_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fso_name ON public.fso USING btree (fso_name);


--
-- Name: idx_inspection_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inspection_code ON public.inspection USING btree (inspection_code);


--
-- Name: idx_inspection_compliance_deadline; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inspection_compliance_deadline ON public.inspection USING btree (compliance_deadline);


--
-- Name: idx_inspection_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inspection_date ON public.inspection USING btree (inspection_date);


--
-- Name: idx_inspection_fso_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inspection_fso_name ON public.inspection USING btree (fso_name);


--
-- Name: idx_inspection_photos_adjudication_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inspection_photos_adjudication_id ON public.inspection_photos USING btree (adjudication_id);


--
-- Name: idx_sample_billed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sample_billed ON public.sample USING btree (billed);


--
-- Name: idx_sample_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sample_code ON public.sample USING btree (sample_code);


--
-- Name: idx_sample_collection_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sample_collection_date ON public.sample USING btree (collection_date);


--
-- Name: idx_sample_fso_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sample_fso_name ON public.sample USING btree (fso_name);


--
-- Name: ix_record_audit_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_record_audit_record_id ON public.record_audit USING btree (record_id);


--
-- Name: ix_record_audit_record_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_record_audit_record_type ON public.record_audit USING btree (record_type);


--
-- Name: ix_record_audit_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_record_audit_timestamp ON public.record_audit USING btree ("timestamp");


--
-- Name: ix_record_audit_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_record_audit_user_id ON public.record_audit USING btree (user_id);


--
-- Name: ix_user_username; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_user_username ON public."user" USING btree (username);


--
-- Name: bill_sample bill_sample_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bill_sample
    ADD CONSTRAINT bill_sample_bill_id_fkey FOREIGN KEY (bill_id) REFERENCES public.bills(id);


--
-- Name: bill_sample bill_sample_sample_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bill_sample
    ADD CONSTRAINT bill_sample_sample_id_fkey FOREIGN KEY (sample_id) REFERENCES public.sample(id);


--
-- Name: case_files case_files_sample_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.case_files
    ADD CONSTRAINT case_files_sample_id_fkey FOREIGN KEY (sample_id) REFERENCES public.sample(id) ON DELETE SET NULL;


--
-- Name: fbo_issue_audit fbo_issue_audit_issue_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fbo_issue_audit
    ADD CONSTRAINT fbo_issue_audit_issue_id_fkey FOREIGN KEY (issue_id) REFERENCES public.fbo_issue(id);


--
-- Name: inspection inspection_adjudication_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspection
    ADD CONSTRAINT inspection_adjudication_id_fkey FOREIGN KEY (adjudication_id) REFERENCES public.adjudications(id) ON DELETE SET NULL;


--
-- Name: inspection inspection_fso_name_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspection
    ADD CONSTRAINT inspection_fso_name_fkey FOREIGN KEY (fso_name) REFERENCES public.fso(fso_name);


--
-- Name: inspection_photos inspection_photos_adjudication_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspection_photos
    ADD CONSTRAINT inspection_photos_adjudication_id_fkey FOREIGN KEY (adjudication_id) REFERENCES public.adjudications(id) ON DELETE CASCADE;


--
-- Name: photo_evidence photo_evidence_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.photo_evidence
    ADD CONSTRAINT photo_evidence_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.case_files(id);


--
-- Name: photo_evidence photo_evidence_inspection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.photo_evidence
    ADD CONSTRAINT photo_evidence_inspection_id_fkey FOREIGN KEY (inspection_id) REFERENCES public.inspection(id);


--
-- Name: record_audit record_audit_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_audit
    ADD CONSTRAINT record_audit_user_id_fkey FOREIGN KEY (user_id) REFERENCES public."user"(id) ON DELETE SET NULL;


--
-- Name: sample sample_fso_name_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sample
    ADD CONSTRAINT sample_fso_name_fkey FOREIGN KEY (fso_name) REFERENCES public.fso(fso_name);


--
-- PostgreSQL database dump complete
--

\unrestrict NItvkvaCRAcLFQD9Rv2d1PQTEjDnY8vTaMqL1Df2RWa1k3M8V7QnE5SqhqRbKf3

