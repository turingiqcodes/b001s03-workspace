-- Copy the authors loaded into stage.authors into public.authors.
-- Authors that already exist (same au_id) are updated to match stage.
INSERT INTO public.authors (au_id, au_lname, au_fname, phone, address, city, state, zip, contract)
SELECT au_id, au_lname, au_fname, phone, address, city, state, zip, contract
FROM stage.authors
ON CONFLICT (au_id) DO UPDATE SET
    au_lname = EXCLUDED.au_lname,
    au_fname = EXCLUDED.au_fname,
    phone    = EXCLUDED.phone,
    address  = EXCLUDED.address,
    city     = EXCLUDED.city,
    state    = EXCLUDED.state,
    zip      = EXCLUDED.zip,
    contract = EXCLUDED.contract;
