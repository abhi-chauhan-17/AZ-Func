import numpy as np
import pandas as pd
import azure.functions as func
import logging
import os
import datetime as dt
from tqdm import tqdm
from joblib import load
import time
import string
from datetime import datetime, timedelta
import warnings
#from pandasql import sqldf
warnings.filterwarnings("ignore")
import urllib
import re
import copy
import itertools
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from typing import Iterable
from azure.identity import DefaultAzureCredential
import struct
import pyodbc
from sqlalchemy import create_engine

#from db_connection import get_db_connection,blob_connection_orduration,uploadBlobStorage

#helper to iterate through all search phrases
TOKEN_L = r'(?<![A-Za-z0-9])'
TOKEN_R = r'(?![A-Za-z0-9])'

def _compile_token(term: str, ignore_case: bool) -> re.Pattern:
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(TOKEN_L + re.escape(term) + TOKEN_R, flags)

def _compile_neg_before(term: str, negatives: Iterable[str]) -> re.Pattern:
    """
    Build a pattern like:  (?<![A-Za-z0-9])(?:no|denies|negative|pre|diet controlled)
                           [\s\-\:\(\)\[\]\.\,\/]*  <term>  (token bounded)
    Allows punctuation or no space between negative and the term.
    """
    neg_alt = "|".join(re.escape(n) for n in negatives if n)
    # Allow multiple separators between negative and the term
    sep = r'[\s\-\:\(\)\[\]\.\,\/]*'
    return re.compile(
        TOKEN_L + r'(?:' + neg_alt + r')' + TOKEN_R + sep +
        TOKEN_L + re.escape(term) + TOKEN_R,
        re.IGNORECASE  # negatives are checked case-insensitively
    )
def BlobSearcher(searchString, positive, synonym_list, negative):
    """
    Returns 1 if a token-level hit on `positive` (case-insensitive) or any synonym
    (case-sensitive) is present, UNLESS negated by a preceding negative term.
    Otherwise returns 0.
    """
    if not isinstance(searchString, str):
        return 0

    text = searchString

    # --- Compile positive patterns (token-aware) ---
    pos_pat = _compile_token(positive, ignore_case=True)
    syn_pats = [_compile_token(s, ignore_case=False) for s in (synonym_list or [])]

    # --- Quick positive scan ---
    foundFlag = 0
    if pos_pat.search(text):
        foundFlag = 1
    else:
        for p in syn_pats:
            if p.search(text):
                foundFlag = 1
                break

    if not foundFlag:
        return 0

    # --- Build negation patterns for each term (positive + synonyms) ---
    negatives = negative or []
    # If you want "Pre" to negate "pre-diabetic"/"pre diabetes", keep it in the negatives list.
    # Otherwise, remove "Pre" to avoid over-negation.
    terms = [positive] + list(synonym_list or [])
    neg_pats = []
    for t in terms:
        # For synonyms, keep case sensitivity of the *term* by embedding it literally
        # but we still do case-insensitive overall; token boundaries protect the term.
        neg_pats.append(_compile_neg_before(t, negatives))

    # --- If any "negative + term" occurs, cancel the hit ---
    for npat in neg_pats:
        if npat.search(text):
            return 0

    return 1


def NoteSplitter(PreOpNote_Unformatted):
    """
    Original NoteSplitter - Split the note into:
      - FamilyHistory: Family History section text
      - HomeMedHx: Home Medications section text
      - NoteRemainder: everything else, with Family History and HomeMedHx removed
    """
    if not isinstance(PreOpNote_Unformatted, str):
        return {"FamilyHistory": "", "HomeMedHx": "", "NoteRemainder": ""}

    text = PreOpNote_Unformatted
    
    REMOVE_LITERAL = (
        "For the purpose of classification, fasting Glucose from 100-125 mg/dl "
        "is considered impaired fasting Glucose (Pre-Diabetic) by the American Diabetes Association.   "
        "Fasting Glucose &gt; 125 mg/dl is indicative of Diabetes Mellitus, but must be confirmed."
    )

    # Case-insensitive literal removal on the original text
    text = re.sub(re.escape(REMOVE_LITERAL), "", PreOpNote_Unformatted, flags=re.IGNORECASE)
    low  = text.lower()

    spans_to_remove = []

    # --- Family History ---
    fam_text = ""
    fam_start = low.find("family history")
    fam_end = -1
    if fam_start >= 0:
        for marker in [
            "social history", "review of systems", "allergies",
            "home medication list", "current outpatient medications",
            "prior to admission medications", "scheduled medications",
            "objective", "labs", "imaging", "anesthesia physical exam"
        ]:
            idx = low.find(marker, fam_start+20)
            if idx >= 0:
                fam_end = idx
                break
    if fam_start >= 0 and fam_end > fam_start:
        fam_text = text[fam_start:fam_end]
        spans_to_remove.append((fam_start, fam_end))
    elif fam_start >= 0:
        fam_text = text[fam_start:]
        spans_to_remove.append((fam_start, len(text)))

    # --- Home Medications ---
    med_text = ""
    med_start = -1
    for marker in [
        "home medication list", "current outpatient medications",
        "prior to admission medications", "scheduled medications"
    ]:
        idx = low.find(marker)
        if idx >= 0:
            med_start = idx
            break

    med_end = -1
    if med_start >= 0:
        for marker in [
            "facility meds", "objective", "allergies", "review of patient's allergies",
            "inpatient:", "labs", "imaging", "anesthesia physical exam"
        ]:
            idx = low.find(marker, med_start+20)
            if idx >= 0:
                med_end = idx
                break
    if med_start >= 0 and med_end > med_start:
        med_text = text[med_start:med_end]
        spans_to_remove.append((med_start, med_end))
    elif med_start >= 0:
        med_text = text[med_start:]
        spans_to_remove.append((med_start, len(text)))

    # --- Build NoteRemainder (remove spans) ---
    spans_to_remove.sort(key=lambda x: x[0])
    remainder_parts = []
    last = 0
    for s, e in spans_to_remove:
        remainder_parts.append(text[last:s])
        last = e
    remainder_parts.append(text[last:])
    note_remainder = "".join(remainder_parts).strip()

    return {
        "FamilyHistory": fam_text.strip(),
        "HomeMedHx": med_text.strip(),
        "NoteRemainder": note_remainder
    }


# =============================================================================
# VM GOLD LOCALIZATION
# =============================================================================
# VM Gold notes have NO discrete Family History or Home Medication List sections.
# Meds appear inline within ROS subsystem comments (e.g. "daily symbicort",
# "on warfarin", "olanzapine, hydroxyzine, sertraline"). Structured HomeMed/
# HomeMedMapping Clarity tables handle drug detection; HomeMedHx is left empty.
#
# Three note formats exist at this site:
#   Format A: "Relevant Problems" with (+)/(-) system headers (majority)
#   Format B: "Past Medical History" with [x] checkbox entries (TKA/THA notes)
#   Format C: "Anesthesia Evaluation" with free-text system review
#
# NoteRemainder captures from the first clinical marker through Physical Exam
# or Anesthesia Plan, stripping out the patient/procedure header and exam/plan.
#
# KNOWN LIMITATION: (-) tagged items (e.g. "(-) Diabetes mellitus") will fire
# as positive hits in BlobSearcher because (-) is not a recognized negation
# prefix. This is mitigated by the CombinedDF merge with MedHx/ProblemList
# structured data (where negatives are excluded), but may produce false
# positives for conditions only mentioned as (-) in the note. If this becomes
# a problem, add "(-)" to the negatives list or pre-strip (-) lines.
# =============================================================================

def NoteSplitter_VMGold(PreOpNote_Unformatted):
    """
    VM Gold site-specific note splitter.

    Returns:
      - FamilyHistory: "" (not present as discrete section at this site)
      - HomeMedHx: "" (meds are inline in ROS; rely on structured Clarity tables)
      - NoteRemainder: text from "Relevant Problems" / "Past Medical History" /
                       "Anesthesia Evaluation" up to "Physical Exam" or
                       "Anesthesia Plan", with pre-diabetes boilerplate removed.
    """
    if not isinstance(PreOpNote_Unformatted, str):
        return {"FamilyHistory": "", "HomeMedHx": "", "NoteRemainder": ""}

    text = PreOpNote_Unformatted

    # Remove the pre-diabetes boilerplate (carried over from original)
    REMOVE_LITERAL = (
        "For the purpose of classification, fasting Glucose from 100-125 mg/dl "
        "is considered impaired fasting Glucose (Pre-Diabetic) by the American Diabetes Association.   "
        "Fasting Glucose &gt; 125 mg/dl is indicative of Diabetes Mellitus, but must be confirmed."
    )
    text = re.sub(re.escape(REMOVE_LITERAL), "", text, flags=re.IGNORECASE)

    low = text.lower()

    # --- Find START of clinical content ---
    # Priority: "Relevant Problems" > "Past Medical History" > "Anesthesia Evaluation"
    start = -1
    for marker in ["relevant problems", "past medical history", "anesthesia evaluation"]:
        idx = low.find(marker)
        if idx >= 0:
            start = idx
            break

    if start < 0:
        # Fallback: use full text (shouldn't happen with well-formed VM Gold notes)
        return {"FamilyHistory": "", "HomeMedHx": "", "NoteRemainder": text.strip()}

    # --- Find END of clinical content ---
    # Stop before Physical Exam or Anesthesia Plan, whichever comes first
    end = len(text)
    search_from = start + 20  # skip past the start marker itself
    for marker in ["physical exam", "anesthesia plan"]:
        idx = low.find(marker, search_from)
        if idx >= 0 and idx < end:
            end = idx

    note_remainder = text[start:end].strip()

    return {
        "FamilyHistory": "",
        "HomeMedHx": "",
        "NoteRemainder": note_remainder
    }


def create_aggregate_string(row, columns):
    """
    Creates a semicolon-separated string of column names where the row value is 1.
    """
    return ';'.join(columns[row == 1])

def CompositeProblemList(row):
    toReturn = ''
    # Exclude the final concatenated string column from the loop
    problem_columns = row.index.drop('Concatenated_Problems', errors='ignore')
    for problem in problem_columns:
        if row[problem] == 1:
            if len(toReturn) > 0:
                toReturn += ';'
            toReturn += problem
    return toReturn

def ros_calculation(con,engine,start_dt_str, stop_dt_str):
    start = time.time()
    #fetch_date = datetime.now().date() - timedelta(days=14)
    #fetch_date = fetch_date.strftime('%Y-%m-%d')
    
    # --- DATA FETCHING ---
    HBA1C_sql = """
      select E.ProcID,
      max(Try_Convert(FLoat, LabValue)) as HBA1CValue,
      Case when max(TRY_CONVERT(Float,LabValue)) > 8 then 1 else 0 END as Diabetes
      from dbo.EventTimes E
      JOIN dbo.A1C A1 on E.ProcID = A1.ProcID AND E.DateofService > ? and E.DateofService < ?
      GROUP BY E.ProciD
    """
    HBA1C_Diabetes = pd.read_sql(HBA1C_sql,con,params=[start_dt_str,stop_dt_str])

    sql_notes = """
    SELECT ProcID, NoteContent as PreOpNote_Unformatted
    FROM (
        select NPM.ProcID, NoteContent, Row_NUMBER() OVER (Partition by NPM.ProcID order by NoteEntryDTTM desc) as rownum
        from dbo.NoteProcedureMapping NPM
        join (select Distinct ProcID from dbo.EventTimes Where DateofService > ? and DateofService < ?) as ET on NPM.procid = ET.ProcID 
        JOIN dbo.Note as N on N.NoteID =NPM.NoteID
        where NoteName IN ('Anesthesia Preprocedure Evaluation')
    ) as i1
    where rownum = 1
    """
    ASA_data = pd.read_sql(sql_notes,con,params=[start_dt_str,stop_dt_str])

    sql_search_phrases = "SELECT * from dbo.ROS_Parsing_SearchPhrases"
    searchFile = pd.read_sql(sql_search_phrases,con)

    sql_medhx = """
    Select ProcID, string_agg(DiagnosisName, ';') as Diagnoses
    FROM (
        select mh.ProcID, mh.MedHxDiagnosisID, MhD.DiagnosisName from dbo.MedHx as mh
        join dbo.MedHxDiagnosis as MhD on mh.MedHxDiagnosisID = mhd.MedHxDiagnosisID
        join (select Distinct ProcID from dbo.EventTimes Where DateofService >  ? and DateofService <  ?) as ET on mh.procid = ET.ProcID 
    ) as i1
    group by ProcID
    """

    medHxData = pd.read_sql(sql_medhx,con,params=[start_dt_str,stop_dt_str])
    medHxData['Diagnoses'] = medHxData['Diagnoses'].fillna('')

    sql_homed_list = "select * from dbo.SelfServeHomeMedClassification"
    HomeMedList = pd.read_sql(sql_homed_list,con)

    sql_problem_list = """
    SELECT P.ProcID, string_agg(CAST(PL.ProblemDiagnosisName as NVARCHAR(MAX)), ';') as ProblemList
    FROM [dbo].[Problem] as P
    join dbo.ProblemList as PL on P.ProblemListID = PL.ProblemListID
    join (select Distinct ProcID from dbo.EventTimes Where DateofService >  ? and DateofService < ?) as ET on P.procid = ET.ProcID 
    group by P.ProcID
    """

    ProblemListData = pd.read_sql(sql_problem_list,con,params=[start_dt_str,stop_dt_str])
    ProblemListData['ProblemList'] = ProblemListData['ProblemList'].fillna('')

    sql_structured_meds = """select hmm.ProcID, STRING_AGG(cast(hm.MedName as nvarchar(max)),';') as HomeMeds_Direct
    from dbo.HomeMed as hm
    join dbo.HomeMedMapping as hmm on hm.HomeMedID = hmm.HomeMedID
    join (select Distinct ProcID from dbo.EventTimes Where DateofService >  ? and DateofService < ?) as ET on hmm.procid = ET.ProcID 
    group by hmm.ProcID"""

    HomeMed_Mapping = pd.read_sql(sql_structured_meds,con,params=[start_dt_str,stop_dt_str])
    HomeMed_Mapping['HomeMeds_Direct'] = HomeMed_Mapping['HomeMeds_Direct'].fillna('')

    # --- PARSED_ROS_HOMEMEDS LOGIC ---
    logging.info("Starting Parsed_ROS_HomeMeds processing...")

    # 1. PARSE INDIVIDUAL DRUGS
    ASA_data_meds = ASA_data[['ProcID', 'PreOpNote_Unformatted']].copy()
    # VM GOLD: NoteSplitter_VMGold returns empty HomeMedHx; drug detection relies on
    # structured HomeMed_Mapping data. The HomeMedHx column will be empty for all rows,
    # so BlobSearcher on it returns 0 — the direct_found path carries all weight.
    ASA_data_meds['HomeMedHx'] = ASA_data_meds.PreOpNote_Unformatted.apply(NoteSplitter_VMGold).apply(lambda x: x['HomeMedHx'])
    ASA_data_meds = ASA_data_meds.set_index('ProcID')
    HomeMed_Mapping_indexed = HomeMed_Mapping.set_index('ProcID')

    HomeMedHx_Parsed = pd.DataFrame(index=ASA_data_meds.index)

    for _, row in tqdm(HomeMedList.iterrows(), total=len(HomeMedList), desc="Searching for drugs"):
        drug_name = row['DrugName']
        note_found = ASA_data_meds['HomeMedHx'].apply(BlobSearcher, positive=drug_name, synonym_list=[], negative=[])
        direct_found = HomeMed_Mapping_indexed['HomeMeds_Direct'].apply(BlobSearcher, positive=drug_name, synonym_list=[], negative=[])
        HomeMedHx_Parsed[drug_name] = (note_found.add(direct_found, fill_value=0) > 0).astype(int)

    # 2. AGGREGATE DRUGS INTO CATEGORIES
    drug_to_category_map = HomeMedList.set_index('DrugName')['Category']
    unique_categories = HomeMedList['Category'].unique()
    Category_Results = pd.DataFrame(0, index=HomeMedHx_Parsed.index, columns=unique_categories)

    for drug_name in tqdm(HomeMedHx_Parsed.columns, desc="Aggregating categories"):
        if drug_name in drug_to_category_map.index:
            category = drug_to_category_map[drug_name]
            if category in Category_Results.columns:
                Category_Results[category] += HomeMedHx_Parsed[drug_name]

    Category_Results = (Category_Results > 0).astype(int)

    # 3. CREATE FINAL OUTPUT TABLE
    category_columns = Category_Results.columns
    Category_Results['Aggregate_Drug_Type_String'] = Category_Results.apply(create_aggregate_string, args=(category_columns,), axis=1)
    Parsed_ROS_HomeMeds_Final = Category_Results.reset_index()

    # 4. WRITE TO DB
    #Parsed_ROS_HomeMeds_Final.to_sql('Parsed_ROS_HomeMeds', engine, if_exists='replace', index=False, schema='stg')
    table_name = 'Parsed_ROS_HomeMeds'
    max_retries = 3

    for attempt in range(max_retries):
            try:
                # Use a transaction block for the operations
                with engine.connect() as connection:
                    with connection.begin() as transaction:
                        # 1. Truncate the table first
                        print(f"Attempt {attempt + 1}: Truncating table stg.{table_name}...")
                        connection.execute(text(f"TRUNCATE TABLE stg.{table_name}"))
                        
                        # 2. Append the new data from the DataFrame
                        print(f"Appending data to {table_name}...")
                        Parsed_ROS_HomeMeds_Final.to_sql(
                            name=table_name,
                            con=connection,
                            if_exists='append',
                            index=False,
                            schema ='stg'
                        )
                
                print("Data loaded successfully.")
                break # Exit loop if successful

            except DBAPIError as e:
                # Check if the error is a deadlock (error code 1205)
                if e.orig and '1205' in str(e.orig):
                    if attempt < max_retries - 1:
                        print(f"Deadlock detected on attempt {attempt + 1}. Retrying in 5 seconds...")
                        time.sleep(5) # Wait before retrying
                    else:
                        print("Final attempt failed due to a deadlock.")
                        raise # Re-raise the exception after the final attempt
                else:
                    # It's a different database error, so don't retry
                    print("A non-deadlock database error occurred.")
                    raise
    logging.info(f"Wrote {len(Parsed_ROS_HomeMeds_Final)} records to stg.Parsed_ROS_HomeMeds.")

    # --- PARSED_ROS_PROBLEMLIST LOGIC ---
    logging.info("Starting Parsed_ROS_ProblemList processing...")

    # VM GOLD: Use NoteSplitter_VMGold — extracts clinical content between
    # "Relevant Problems" and "Physical Exam", returns empty FamilyHx/HomeMedHx
    ASA_data['SplitStrings'] = ASA_data.PreOpNote_Unformatted.apply(NoteSplitter_VMGold)
    ASA_data['NoteRemainder'] = ASA_data['SplitStrings'].apply(lambda x: x['NoteRemainder'])
    ASA_data['FamilyHx'] = ASA_data['SplitStrings'].apply(lambda x: x['FamilyHistory'])

    ASA_data = ASA_data.set_index('ProcID')

    searchFile['Alternate names_2'] = searchFile['Alternate names'].astype(str).apply(lambda x: x.split(', '))
    searchFile['Negative prefix keywords_2'] = searchFile['Negative prefix keywords'].astype(str).apply(lambda x: x.split(', '))
    
    for ind, row in tqdm(searchFile.iterrows()):
        ASA_data[row['Problem']] = ASA_data['NoteRemainder'].apply(BlobSearcher, positive=row['Problem'], synonym_list=row['Alternate names_2'], negative=row['Negative prefix keywords_2'])
        medHxData[row['Problem']] = medHxData['Diagnoses'].apply(BlobSearcher, positive=row['Problem'], synonym_list=row['Alternate names_2'], negative=['pre-','pre', 'family history of', 'family history', 'gestational'] if row.Problem == 'Diabetes' else [])
        ProblemListData[row['Problem']] = ProblemListData['ProblemList'].apply(BlobSearcher, positive=row['Problem'], synonym_list=row['Alternate names_2'], negative=['pre-','pre', 'family history of', 'family history', 'gestational'] if row.Problem == 'Diabetes' else [])

    #Removed category specific note splicing: all terms hit note remainder, problem list, and med hx for now
    #for ind, row in tqdm(searchFile[searchFile['Problem Category'] == 'PONV'].iterrows()):
    #    ASA_data[row['Problem']] = ASA_data['NoteRemainder'].apply(BlobSearcher, positive=row['Problem'], synonym_list=row['Alternate names_2'], negative=row['Negative prefix keywords_2'])
    #
    #for ind, row in tqdm(searchFile[searchFile['Problem Category'] == 'Anesthetic Complication'].iterrows()):
    #    ASA_data[row['Problem']] = ASA_data['NoteRemainder'].apply(BlobSearcher, positive=row['Problem'], synonym_list=row['Alternate names_2'], negative=row['Negative prefix keywords_2']) 
    #
    #for ind, row in tqdm(searchFile[searchFile['Problem Category'] == 'Surgical Hx'].iterrows()):
    #    ASA_data[row['Problem']] = ASA_data['NoteRemainder'].apply(BlobSearcher, positive=row['Problem'], synonym_list=row['Alternate names_2'], negative=row['Negative prefix keywords_2']) 

    # Use errors='ignore' to prevent crashes if columns don't exist
    ASA_data.drop(['PONV', 'Motion Sickness', 'Relevant Surgical PONV'], inplace=True, axis=1, errors='ignore')

    ASA_data.reset_index(inplace=True)

    list_toWrite = list(searchFile['Problem'])
    # Use a loop with a check to safely remove items
    for item in ['PONV', 'Motion Sickness', 'Relevant Surgical PONV']:
        if item in list_toWrite:
            list_toWrite.remove(item)

    CombinedDF = ASA_data.set_index('ProcID')[list_toWrite] \
                    .add(medHxData.set_index('ProcID')[list(searchFile['Problem'])],fill_value=0) \
                    .add(ProblemListData.set_index('ProcID')[list(searchFile['Problem'])],fill_value=0) \
                    .add(HBA1C_Diabetes.set_index('ProcID')[['Diabetes']], fill_value=0)

    for col in CombinedDF.columns:
        CombinedDF[col] = CombinedDF[col].apply(lambda x: 1 if x > 0 else 0)

    CombinedDF['Concatenated_Problems'] = CombinedDF.apply(CompositeProblemList,axis=1)

    # Final write to DB for ProblemList
    Parsed_ROS_ProblemList_Final = CombinedDF.reset_index()
    # Parsed_ROS_ProblemList_Final.drop_duplicates(subset='ProcID').to_sql('Parsed_ROS_ProblemList', engine, if_exists='replace',index=False, schema='stg')
    Parsed_ROS_ProblemList_Final_df = Parsed_ROS_ProblemList_Final.drop_duplicates(subset='ProcID')
    table_name = 'Parsed_ROS_ProblemList'
    max_retries = 3

    for attempt in range(max_retries):
            try:
                # Use a transaction block for the operations
                with engine.connect() as connection:
                    with connection.begin() as transaction:
                        # 1. Truncate the table first
                        print(f"Attempt {attempt + 1}: Truncating table stg.{table_name}...")
                        connection.execute(text(f"TRUNCATE TABLE stg.{table_name}"))
                        
                        # 2. Append the new data from the DataFrame
                        print(f"Appending data to {table_name}...")
                        Parsed_ROS_ProblemList_Final_df.to_sql(
                            name=table_name,
                            con=connection,
                            if_exists='append',
                            index=False,
                            schema ='stg'
                        )
                
                print("Data loaded successfully.")
                break # Exit loop if successful

            except DBAPIError as e:
                # Check if the error is a deadlock (error code 1205)
                if e.orig and '1205' in str(e.orig):
                    if attempt < max_retries - 1:
                        print(f"Deadlock detected on attempt {attempt + 1}. Retrying in 5 seconds...")
                        time.sleep(5) # Wait before retrying
                    else:
                        print("Final attempt failed due to a deadlock.")
                        raise # Re-raise the exception after the final attempt
                else:
                    # It's a different database error, so don't retry
                    print("A non-deadlock database error occurred.")
                    raise

    logging.info(f"Wrote {len(Parsed_ROS_ProblemList_Final_df)} records to stg.Parsed_ROS_ProblemList.")

    # --- KICK OFF SPROC ---
    logging.info("Executing [dbo].[Upsert_ParsedROS] stored procedure...")
    crsr = engine.raw_connection().cursor()
    crsr.execute('exec [dbo].[Upsert_ParsedROS]')
    crsr.commit()
    crsr.close()
    logging.info("Stored procedure execution complete.")