import numpy as np
import pandas as pd
import azure.functions as func
import logging
import os
import pyodbc
import struct
import urllib
from datetime import datetime, timedelta
import datetime as dt
from tqdm import tqdm, tqdm_notebook
import time
import re
import copy
from sqlalchemy import create_engine
from tqdm import tqdm
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from azure.identity import DefaultAzureCredential

#append the strings which are exists in list 
def aggNoNones(stringList):
    return '; '.join(x for x in stringList if x)

def Redose_Compliance(AMD_Joined_Row):
    prev_time = AMD_Joined_Row['Time_x']
    next_time = AMD_Joined_Row['Time_y']
    Threshold = AMD_Joined_Row['RedoseThreshold']
    Case_End_Time = AMD_Joined_Row['StopTime']
    
    #if there is no redose threshold for the drug, return pass
    if pd.isna(Threshold):
        return {"Failure": 0,
                  "FailureTime":'',
                  "FailureDrug":'',
                  "RedoseDrug":AMD_Joined_Row['DrugShortName'],
                  "FailureReason_Details":'',
                  "Redose_Summary":'{0} has no redose requirement'.format(AMD_Joined_Row['DrugShortName']),
           "FailureReason_Simple": 'Dose given on time'}
    #If there is no redose
    if pd.isna(next_time):
        Threshold = int(Threshold)
        case_end_delta = (Case_End_Time - prev_time).total_seconds()/60
        #if the case ends more than 30 minutes after the redose should have occured, count it as a fail.
        if (case_end_delta > (Threshold+30)):
            return {"Failure": 1,
                       "FailureTime":prev_time.strftime('%Y-%m-%d %H:%M:%S'),
                  "FailureDrug":AMD_Joined_Row['DrugShortName'],
                    "RedoseDrug":AMD_Joined_Row['DrugShortName'],
                    "FailureReason_Details":"No redose, case end after {0} minutes".format(int(case_end_delta)),
                  "Redose_Summary":"No redose of {1} (threshold of {2} minutes) after previous dose at {3}, case ends {0} minutes after previous dose".format(int(case_end_delta), AMD_Joined_Row['DrugShortName'], Threshold, prev_time.strftime("%H:%M")),
           "FailureReason_Simple": 'Dose not given'}
        #Case has ended within 30 mins of redose schedule, no fail
        else:
            return {"Failure": 0,
                       "FailureTime":'',
                  "FailureDrug":'',
                    "RedoseDrug":AMD_Joined_Row['DrugShortName'],
                    "FailureReason_Details":'',
                  "Redose_Summary":'{0} was given at {1} and case ends at {2}, which is within redose threshold'.format(AMD_Joined_Row['DrugShortName'], prev_time.strftime("%H:%M"), Case_End_Time.strftime("%H:%M")),
           "FailureReason_Simple": 'Dose given on time'}
    
    #If there is a redose time to assess for compliance and that time comes later than redose threshold + 15 mins, fail
    if (prev_time + timedelta(minutes=Threshold+15)) < next_time:
        Threshold = int(Threshold)
        return {"Failure": 1,
                  "FailureTime":prev_time.strftime('%Y-%m-%d %H:%M:%S'),
                  "FailureDrug":AMD_Joined_Row['DrugShortName'],
                "RedoseDrug":AMD_Joined_Row['DrugShortName'],
                "FailureReason_Details":"Redosed at {0} minutes, longer than padded threshold of {1}".format(round((next_time-prev_time).total_seconds()/60),Threshold+15),
                  "Redose_Summary":"{2} originally dosed at {3} and redosed after {0} minutes, longer than padded threshold of {1}".format(round((next_time-prev_time).total_seconds()/60),Threshold+15,AMD_Joined_Row['DrugShortName'], prev_time.strftime("%H:%M")),
           "FailureReason_Simple": 'Dose given late'}
    
    #If there is a redose time, and the redose comes more than 30 minutes before the scheduled reduce
    if ((prev_time + timedelta(minutes=Threshold-30)) > next_time) & ((prev_time + timedelta(minutes=120)) < next_time):
        Threshold = int(Threshold)
        return {"Failure": 0,
                   "FailureTime":prev_time.strftime('%Y-%m-%d %H:%M:%S'),
                  "FailureDrug":AMD_Joined_Row['DrugShortName'],
                "RedoseDrug":AMD_Joined_Row['DrugShortName'],
                "FailureReason_Details":"Early redose warning: redosed at {0} minutes, short of padded threshold of {1}".format(round((next_time-prev_time).total_seconds()/60),Threshold-15),
                  "Redose_Summary":"{2} originally dosed at {3} and redosed at {4}: early redose warning as this is short of padded threshold of {1}".format(round((next_time-prev_time).total_seconds()/60),Threshold-15, AMD_Joined_Row['DrugShortName'], prev_time.strftime("%H:%M"), next_time.strftime("%H:%M")),
           "FailureReason_Simple": 'Dose given early'}
    return {"Failure": 0,
                       "FailureTime":'',
                  "FailureDrug":'',
                  "RedoseDrug":AMD_Joined_Row['DrugShortName'],
                    "FailureReason_Details":'',
                  "Redose_Summary":'{0} was originally dosed at {1} and redosed at {2}, which is within threshold'.format(AMD_Joined_Row['DrugShortName'], prev_time.strftime("%H:%M"), next_time.strftime("%H:%M")),
           "FailureReason_Simple": 'Dose given on time'}           
           

def Initial_dose_Compliance_Success(RowSet):
    Valid_Doses = RowSet[RowSet.Valid_ForInitialCompliance]
    if len(Valid_Doses) == 0:
        return {"Failure": 1,
                       "FailureTime":RowSet['Time'].min().strftime('%Y-%m-%d %H:%M:%S'),
                  "FailureDrug":'',
                  "FailureReason_Details":"No dose in valid time range",
       "FailureReason_Simple": 'Dose not given'}
    SuccessFlag = 0
    Fail = None
    for ind,dose in Valid_Doses.sort_values('Time',ascending=False).iterrows():
        if dose['Time'] > dose['StartTime']:
            Fail = {"Failure": 1,
                       "FailureTime":dose['Time'].strftime('%Y-%m-%d %H:%M:%S'),
                  "FailureDrug":dose['DrugShortName'],
                  "FailureReason_Details":"Initial dose after {0} minutes".format(int(round((dose['Time']-dose['StartTime']).total_seconds()/60,0))),
                   "FailureReason_Simple": 'Dose given late'}
        elif pd.isna(dose['InitialDoseThreshold']) & (dose['Time'] < dose['StartTime']-timedelta(minutes=60)):
            Fail = {"Failure": 1,
                           "FailureTime":dose['Time'].strftime('%Y-%m-%d %H:%M:%S'),
                      "FailureDrug":dose['DrugShortName'],
                      "FailureReason_Details":"Initial dose given {0} minutes before procedure start".format(int(round((dose['StartTime'] - dose['Time']).total_seconds()/60,0))),
           "FailureReason_Simple": 'Dose given early'}
        elif not pd.isna(dose['InitialDoseThreshold']):
            if(dose['Time'] < dose['StartTime']-timedelta(minutes=dose['InitialDoseThreshold'])):
                Threshold = int(dose['InitialDoseThreshold'])
                Fail = {"Failure": 1,
                               "FailureTime":dose['Time'].strftime('%Y-%m-%d %H:%M:%S'),
                          "FailureDrug":dose['DrugShortName'],
                          "FailureReason_Details":"Initial dose given {0} minutes before procedure start".format(int(round((dose['StartTime'] - dose['Time'] ).total_seconds()/60,0))),
                           "FailureReason_Simple": 'Dose given early'}
            else:
                return {"Failure": 0,
                           "FailureTime":'',
                      "FailureDrug":'',
                      "FailureReason_Details":'',
               "FailureReason_Simple": 'Dose given on time'}
        else:
            return {"Failure": 0,
                       "FailureTime":'',
                  "FailureDrug":'',
                  "FailureReason_Details":'',
           "FailureReason_Simple": 'Dose given on time'}
    return Fail


def Initial_dose_Compliance(AMD_Joined_Row):
    first_time = AMD_Joined_Row['Time_x']
    Threshold = AMD_Joined_Row['InitialDoseThreshold']
    Case_Start_Time = AMD_Joined_Row['StartTime']
    FirstDose = AMD_Joined_Row['DoseNum_Casewide_x']
    if FirstDose != 1:
        return {"Failure": 0,
                       "FailureTime":'',
                  "FailureDrug":'',
                  "FailureReason_Details":'',
           "FailureReason_Simple": 'Dose given on time'}
    if first_time > Case_Start_Time+timedelta(minutes=60):
        return {"Failure": 1,
                       "FailureTime":first_time.strftime('%Y-%m-%d %H:%M:%S'),
                  "FailureDrug":AMD_Joined_Row['DrugShortName'],
                  "FailureReason_Details":"Initial dose after {0} minutes".format(round((first_time-Case_Start_Time).total_seconds()/60)),
       "FailureReason_Simple": 'Dose not given'}
    if first_time > Case_Start_Time:
        return {"Failure": 1,
                       "FailureTime":first_time.strftime('%Y-%m-%d %H:%M:%S'),
                  "FailureDrug":AMD_Joined_Row['DrugShortName'],
                  "FailureReason_Details":"Initial dose after {0} minutes".format(round((first_time-Case_Start_Time).total_seconds()/60)),
       "FailureReason_Simple": 'Dose given late'}
    if pd.isna(Threshold):
        if first_time < Case_Start_Time-timedelta(minutes=60):
            return {"Failure": 1,
                           "FailureTime":first_time.strftime('%Y-%m-%d %H:%M:%S'),
                      "FailureDrug":AMD_Joined_Row['DrugShortName'],
                      "FailureReason_Details":"Initial dose given {0} minutes before procedure start".format(round((Case_Start_Time - first_time).total_seconds()/60)),
           "FailureReason_Simple": 'Dose given early'}
        else:
            return {"Failure": 0,
                       "FailureTime":'',
                  "FailureDrug":'',
                  "FailureReason_Details":'',
           "FailureReason_Simple": 'Dose given on time'}
    Threshold = int(Threshold)
    if first_time < Case_Start_Time-timedelta(minutes=Threshold):
            return {"Failure": 1,
                           "FailureTime":first_time.strftime('%Y-%m-%d %H:%M:%S'),
                      "FailureDrug":AMD_Joined_Row['DrugShortName'],
                      "FailureReason_Details":"Initial dose given {0} minutes before procedure start".format(round((Case_Start_Time - first_time).total_seconds()/60)),
           "FailureReason_Simple": 'Dose given early'}
    return {"Failure": 0,
                       "FailureTime":'',
                  "FailureDrug":'',
                  "FailureReason_Details":'',
           "FailureReason_Simple": 'Dose given on time'}


def aggSimpleFailReasons(stringList):
    failReasons = ['Dose not given', 'Dose given late', 'Dose given early']
    for reason in failReasons:
        if reason in stringList.values:
            return reason
    return 'Dose given on time'

    Redose_Eligible = Redose_DF.Redose_Eligible.max()
    Redose_DF = Redose_DF[Redose_DF.Redose_Eligible == 1]
    Failure = Redose_DF.Failure.max()
    FailureTime = aggNoNones(Redose_DF.FailureTime)
    FailureDrug = aggNoNones(Redose_DF.FailureDrug)
    FailureReason_Details = aggNoNones(Redose_DF.FailureReason_Details)
    All_Redosed_Drugs = aggNoNones(Redose_DF.RedoseDrug.unique())
    FailureReason_Simple = aggSimpleFailReasons(Redose_DF.FailureReason_Simple)
    Redose_Summary = aggNoNones(Redose_DF.Redose_Summary)
    return {
        'Failure': Failure if not pd.isna(Failure) else 0,
        'FailureTime': FailureTime if not pd.isna(FailureTime) else '',
        'FailureDrug': FailureDrug if not pd.isna(FailureDrug) else '',
        'FailureReason_Details': FailureReason_Details if not pd.isna(FailureReason_Details) else '',
        'FailureReason_Simple': FailureReason_Simple if not pd.isna(FailureReason_Simple) else '',
        'Redose_Summary': Redose_Summary,
        'Redose_Eligible': Redose_Eligible,
        'DrugName': All_Redosed_Drugs
    }

def aggRedosing(Redose_DF):
    #Assemble all redoses into a single case level string
    Redose_Eligible = Redose_DF.Redose_Eligible.max()
    Redose_DF = Redose_DF[Redose_DF.Redose_Eligible == 1]
    Failure = Redose_DF.Failure.max()
    FailureTime = aggNoNones(Redose_DF.FailureTime)
    FailureDrug = aggNoNones(Redose_DF.FailureDrug)
    FailureReason_Details = aggNoNones(Redose_DF.FailureReason_Details)
    All_Redosed_Drugs = aggNoNones(Redose_DF.RedoseDrug.unique())
    FailureReason_Simple = aggSimpleFailReasons(Redose_DF.FailureReason_Simple)
    Redose_Summary = aggNoNones(Redose_DF.Redose_Summary)
    return {
        'Failure': Failure if not pd.isna(Failure) else 0,
        'FailureTime': FailureTime if not pd.isna(FailureTime) else '',
        'FailureDrug': FailureDrug if not pd.isna(FailureDrug) else '',
        'FailureReason_Details': FailureReason_Details if not pd.isna(FailureReason_Details) else '',
        'FailureReason_Simple': FailureReason_Simple if not pd.isna(FailureReason_Simple) else '',
        'Redose_Summary': Redose_Summary,
        'Redose_Eligible': Redose_Eligible,
        'DrugName': All_Redosed_Drugs
    }




### abx dosing call
### time window for abx dosing calculation from date run to 14 day's
# stop_dt = datetime.today()
# start_dt = stop_dt + timedelta(days=-14)

# read necessery procedure event data for further analysis 
b_sql = """
SELECT B.*, BC.BolusShortName FROM dbo.Bolus B
JOIN
(SELECT *
  FROM [dbo].[BolusClass]
  where Class = 'ANTIBIOTICS' OR BolusShortName in (
 'Ampicillin',
'Ampicillin-Sulbactam',
'Cefazolin',
'Cefotetan',
'Cefoxitin',
'Clindamycin',
'Metronidazole',
'Vancomycin',
'Vancomycin A-V',
'Piperacillin-Tazobactam',
'Aztreonam',
'miconazole',
'Levofloxacin',
'Ciprofloxacin')) as BC ON B.BolusClassID = BC.BolusClassID
JOIN
(select ProcID,
ProcedureStart as StartTime,
CASE WHEN ProcedureEnd is not null then ProcedureEnd else DATEADD(minute,-15,LeaveOR) END as StopTime
from dbo.EventTimes WHERE DateofService BETWEEN CAST(? as date) and CAST(? as date)
) as timerestrict on B.ProcID = timerestrict.ProcID
WHERE B.BolusDT BETWEEN DATEADD(minute, -125, StartTime) and StopTime
  """


#read the infusion info for procedures 
i_sql = """
SELECT I.*, IB.InfusionShortName
FROM dbo.Infusion I
JOIN
  (select * from dbo.InfusionBag
  WHERE InfusionShortName IN (
'Ampicillin-Sulbactam',
'Cefazolin',
'Cefotetan',
'Cefoxitin',
'Clindamycin',
'Metronidazole',
'Vancomycin',
'Vancomycin A-V',
'Piperacillin-Tazobactam',
'Aztreonam',
'miconazole',
'Levofloxacin',
'Ciprofloxacin'
  )) as IB ON I.InfusionBagID = IB.InfusionBagID
JOIN
(select ProcID,
ProcedureStart as StartTime,
CASE WHEN ProcedureEnd is not null then ProcedureEnd else DATEADD(minute,-15,LeaveOR) END as StopTime
from dbo.EventTimes WHERE DateofService BETWEEN CAST(? as date) and CAST(? as date)
) as timerestrict on I.ProcID = timerestrict.ProcID
WHERE I.StartTime BETWEEN DATEADD(minute, -125, timerestrict.StartTime) and timerestrict.StopTime
  """

times_sql = """
select ProcID,
ProcedureStart as StartTime,
CASE WHEN ProcedureEnd is not null then ProcedureEnd else DATEADD(minute,-15,CalculatedLeaveOR) END as StopTime
from dbo.EventTimes WHERE DateofService BETWEEN CAST(? as date) and CAST(? as date)
"""
thresholds_sql = """
select * from dbo.ABX_Dosing_Thresholds
"""
Initial_Abx_Ineligible = """
SELECT DISTINCT NP.ProcID,1 AS 'AntibioticNoteFlag'
FROM dbo.ObservationProcedureMapping NP WITH (NOLOCK)
JOIN dbo.EventTimes ET on NP.ProcID = ET.ProcID
LEFT JOIN dbo.Observation N WITH (NOLOCK)
ON NP.ObservationID = N.ObservationID
WHERE N.Observation ='R FHS AN TIMELY ADMIN OF ANTIBIOTICS' AND ObservationValue = 'ABX Not Ordered, Not Indicated'
AND DateofService BETWEEN CAST(? as date) and CAST(? as date)
"""

#Redose_Abx_Ineligible = """
#SELECT DISTINCT NP.ProcID,1 AS 'AntibioticNoteFlag'
#FROM dbo.ObservationProcedureMapping NP WITH (NOLOCK)
#JOIN dbo.EventTimes ET on NP.ProcID = ET.ProcID
#LEFT JOIN dbo.Observation N WITH (NOLOCK)
#ON NP.ObservationID = N.ObservationID
#WHERE N.Observation ='R AN REASON ANTIBIOTIC NOT ADMINISTERED'
#AND DateofService BETWEEN CAST('{0}' as date) and CAST('{1}' as date)
#""".format(start_dt.strftime("%Y-%m-%d"), stop_dt.strftime("%Y-%m-%d"))

    

def ABX_dosing_calc(con,engine, start_dt_str, stop_dt_str):
    #logging.info('DB connected succesfully')
    #read procedure info
    bolus_data = pd.read_sql(b_sql, engine, params=(start_dt_str, stop_dt_str))
    #read infusion at procedure level info
    infusion_data = pd.read_sql(i_sql, engine, params=(start_dt_str, stop_dt_str))
    # read time window info
    times_data = pd.read_sql(times_sql, engine, params=(start_dt_str, stop_dt_str))
    #logging.info('read all necessery data from DB')
    # drop duplicate item's group by ['ProcID', 'BolusClassID', 'Dose', 'DoseUnit', 'BolusDT','BolusShortName']

    bolus_data = bolus_data.drop_duplicates(subset=['ProcID', 'BolusClassID', 'Dose', 'DoseUnit', 'BolusDT',
        'BolusShortName'],keep='first')
    # drop duplicate item's group by ['ProcID', 'InfusionBagID', 'DosageRate', 'DosageRateUnit', 'AmountRate','AmountRateUnit', 'WeightBasedRate', 'WeightBasedRateUnit', 'StartTime','StopTime', 'InfusionShortName']
    infusion_data = infusion_data.drop_duplicates(subset=['ProcID', 'InfusionBagID', 'DosageRate', 'DosageRateUnit', 'AmountRate',
    'AmountRateUnit', 'WeightBasedRate', 'WeightBasedRateUnit', 'StartTime',
    'StopTime', 'InfusionShortName'],keep='first')

    thresholds = pd.read_sql(thresholds_sql,con)
    Ineligible_Initial_Dose = pd.read_sql(Initial_Abx_Ineligible,con, params=[start_dt_str, stop_dt_str])
    #Ineligible_Redose = pd.read_sql(Redose_Abx_Ineligible,con)

    inf_to_Union = infusion_data[['InfusionID','ProcID','InfusionShortName','StartTime']]
    inf_to_Union.columns = ['ID','ProcID','DrugShortName','Time']
    inf_to_Union['Modality'] = 'Infusion'

    bol_to_Union = bolus_data[['BolusID','ProcID','BolusShortName','BolusDT']]
    bol_to_Union.columns = ['ID','ProcID','DrugShortName','Time']
    bol_to_Union['Modality'] = 'Bolus'
    # create all modality info from bol_to_Union and inf_to_Union
    AllModalityData = pd.concat([bol_to_Union, inf_to_Union])
    AllModalityData.sort_values(by=['ProcID','Time'],inplace=True)
    AllModalityData["DoseNum"] = AllModalityData.groupby(["ProcID","DrugShortName"])["Time"].rank(method="first", ascending=True)

    AllModalityData["DoseNum_Casewide"] = AllModalityData.groupby("ProcID")["Time"].rank(method="first", ascending=True)
    AllModalityData["DoseNum_Casewide_Desc"] = AllModalityData.groupby("ProcID")["Time"].rank(method="first", ascending=False)
    AllModalityData_joiner = AllModalityData.copy(deep=True)
    AllModalityData_joiner['DoseNum'] -= 1

    AllModalityData_joined = AllModalityData.merge(AllModalityData_joiner,on=['ProcID','DoseNum','DrugShortName'],how='left')

    AllModalityData_joined = AllModalityData_joined.merge(times_data,on='ProcID',how='left')

    #clean drug name
    thresholds['DrugShortName'] = thresholds.DrugName.str.lower()
    AllModalityData_joined = AllModalityData_joined.merge(thresholds,on='DrugShortName',how='left')

    #establish eligibility for redose based on procedure length:
    AllModalityData_joined['ProcDur_Mins'] = (AllModalityData_joined.StopTime - AllModalityData_joined.StartTime).apply(lambda x: x.total_seconds() / 60.0)
    AllModalityData_joined['Redose_Eligible'] = 1
    AllModalityData_joined['Redose_Eligibility_EndTime'] = AllModalityData_joined \
                                                            .apply(lambda x: x.Time_x + timedelta(minutes=x.RedoseThreshold) \
                                                                if not pd.isna(x.RedoseThreshold) else x.Time_x,axis=1)
    AllModalityData_joined.loc[pd.isna(AllModalityData_joined.RedoseThreshold) | 
                            (AllModalityData_joined['Redose_Eligibility_EndTime']
                            > (AllModalityData_joined.StopTime + timedelta(minutes=-30))), 'Redose_Eligible'] = 0

    #Initial compliance calculation
    AllModalityData_InitialComp = AllModalityData.merge(times_data,on='ProcID',how='left')
    AllModalityData_InitialComp = AllModalityData_InitialComp.merge(thresholds, on='DrugShortName',how='left')

    AllModalityData_InitialComp['Valid_ForInitialCompliance'] = (AllModalityData_InitialComp['Time'] >= (AllModalityData_InitialComp['StartTime'] - timedelta(hours=2))) & \
                                                                (AllModalityData_InitialComp['Time'] <= (AllModalityData_InitialComp['StartTime'] + timedelta(hours=1)))

    # calling Initial_dose_Compliance_Success function at procedure level to identify on time and success or failure status 
    Initial_Dose_Compliance_Success = AllModalityData_InitialComp.groupby('ProcID').apply(Initial_dose_Compliance_Success)
    Columns = ['Failure', 'FailureTime', "FailureDrug","FailureReason_Details","FailureReason_Simple"]

    Initial_Dose_Failures = pd.DataFrame(index = Initial_Dose_Compliance_Success.index, columns = Columns)

    for col in Columns:
        Initial_Dose_Failures[col] = Initial_Dose_Compliance_Success.apply(lambda x: x[col])
    Initial_Dose_Failures.columns = [x+'_Initial' for x in Initial_Dose_Failures.columns]
    Ineligible_Initial_Dose['InitialDoseEligible'] = 0
    Ineligible_Initial_Dose.drop('AntibioticNoteFlag',axis=1,inplace=True)
    Ineligible_Initial_Dose.set_index('ProcID',inplace=True)

    Initial_Dose_Failures = Initial_Dose_Failures.merge(Ineligible_Initial_Dose,left_index=True,right_index=True,how='left')
    Initial_Dose_Failures['InitialDoseEligible'].fillna(1,inplace=True)

    Redose_Compliances = pd.DataFrame.from_records(AllModalityData_joined.apply(Redose_Compliance,axis=1).values,index = AllModalityData_joined.index)
    Redose_Compliances.Failure.value_counts()

    procID_redose = AllModalityData_joined[['ProcID','DoseNum','Redose_Eligible']].join(Redose_Compliances)
    Agg_redose = procID_redose.groupby('ProcID').apply(aggRedosing).apply(pd.Series)

    Agg_redose.columns = [x+'_Redose' for x in Agg_redose.columns]
    Agg_redose['DrugName_Redose'] = Agg_redose['DrugName_Redose'].replace(r'^\s*$', np.nan, regex=True)
    Agg_redose.rename({'Redose_Eligible_Redose':'Eligible_Redose', 'Redose_Summary_Redose': 'RedoseSummary'},axis=1,inplace=True)
    FirstDrug = AllModalityData[AllModalityData.DoseNum_Casewide == 1][['ProcID','DrugShortName', 'Time']].rename({'DrugShortName':'FirstDrug', 'Time':'FirstDrugTime'},axis=1).set_index('ProcID')

    LastDrug = AllModalityData[AllModalityData.DoseNum_Casewide_Desc == 1][['ProcID','DrugShortName', 'Time']].rename({'DrugShortName':'LastDrug', 'Time':'LastDrugTime'},axis=1).set_index('ProcID')

    Output_DF = pd.DataFrame(Initial_Dose_Failures).join(Agg_redose).join(FirstDrug).join(LastDrug).reset_index()

    #writing the ABX calculated results into Database
    #Output_DF.to_sql('ABX_Dosing_Calculation', engine, if_exists='replace', index=False, schema='stg')
    table_name = 'ABX_Dosing_Calculation'
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
                    Output_DF.to_sql(
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
    crsr = engine.raw_connection()
    #writing the ABX calculated results into DBO schema and historical table
    crsr.execute('exec [PeriopInsights].[dbo].[Upsert_ABX_Dosing]')
    crsr.commit()
    logging.info('ABX calculated results are stored in Database')
    #logging.info(Output_DF.shape)


def get_db_connection(local=False):
    if local:
        server = "sqlmi-csco-prod.public.40b12f11c5ef.database.windows.net,3342"
        database = "PeriopInsights"
        driver = "{ODBC Driver 17 for SQL Server}"
        Authentication = "ActiveDirectoryInteractive"

        sql_connection_string = f"Driver={driver};SERVER={server};DATABASE={database};Encrypt=yes;TrustServerCertificate=no;Authentication=ActiveDirectoryInteractive;"
        quoted_conn_str = urllib.parse.quote_plus(sql_connection_string)

        engine = create_engine(f"mssql+pyodbc:///?odbc_connect={quoted_conn_str}", fast_executemany=True)
        conn = pyodbc.connect(sql_connection_string)
        return conn, engine
    
    else:
        server = os.getenv('DB_Server', 'sqlmi-csco-prod.public.40b12f11c5ef.database.windows.net,3342')
        database = os.getenv('database', 'PeriopInsights')
        driver = '{ODBC Driver 18 for SQL Server}'
        managed_identity = os.getenv('MANAGED_IDENTITY_CLIENT_ID', 'fb33bf77-d067-41bd-968a-f15689897aa9')

        print(f"Driver: {driver}, Server: {server}, Database: {database}")

        try:
            # Get Azure credential and token
            credential = DefaultAzureCredential(managed_identity_client_id=managed_identity)
            token = credential.get_token("https://database.windows.net/.default").token.encode("UTF-16-LE")
            token_struct = struct.pack(f'<I{len(token)}s', len(token), token)
            SQL_COPT_SS_ACCESS_TOKEN = 1256

            # Create connection string for SQLAlchemy
            connection_string = f"Driver={driver};SERVER={server};DATABASE={database}"
            quoted_conn_str = urllib.parse.quote_plus(connection_string)
            conn = pyodbc.connect(connection_string, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})
            # Create engine with Azure authentication
            engine = create_engine(
                f"mssql+pyodbc:///?odbc_connect={quoted_conn_str}",
                connect_args={
                    "attrs_before": {SQL_COPT_SS_ACCESS_TOKEN: token_struct}
                },
                pool_pre_ping=True,  # Validate connections before use
                pool_recycle=3600    # Recycle connections every hour
            )

            print('---- DB engine created successfully ---')
            return conn, engine

        except Exception as e:
            print(f"Error creating database engine: {str(e)}")
            raise e
        
if __name__ == "__main__":
    
    con = None
    engine = None
    try:
        logging.info("Attempting to connect to the database...")
        con, engine = get_db_connection(local=True)
        
        if con and engine:
            logging.info("Database connection successful. Starting calculation.")
            end_date = datetime.now().date().strftime('%Y-%m-%d')
            start_date = (datetime.now().date() - timedelta(days=15)).strftime('%Y-%m-%d')
            ABX_dosing_calc(con, engine,start_date,end_date)
            
    except Exception as e:
        logging.error(f"An error occurred during the main execution: {e}")
        
    finally:
        if con:
            con.close()
            logging.info("Database connection closed.")
        if engine:
            engine.dispose()
            logging.info("SQLAlchemy engine disposed.")