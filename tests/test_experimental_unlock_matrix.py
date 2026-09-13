from scripts.build_experimental_unlock_matrix import summarize_cells


def test_unlock_matrix_counts_real_modules_and_counterfactual_upper_bound():
    cells=[{'month':'2022-01','ticker':'AAA','good_count':8,'final_score':None,
            'module_values':{'M2':None,'M1':.6,'M3':.5,'Ek4':.4,'Ek1':.7,'Ek9':.8},
            'reasons':['RAW_CLOSE_BASIS_EVIDENCE_MISSING']},
           {'month':'2022-01','ticker':'BBB','good_count':None,'final_score':None,
            'module_values':{'M2':None,'M1':None,'M3':.5,'Ek4':.4,'Ek1':None,'Ek9':.8},
            'reasons':['HISTORICAL_SECTOR_FAMILY_EVIDENCE_MISSING']}]
    metrics,matrix=summarize_cells(cells)
    assert metrics['cells_with_ge5_modules'] == 1
    raw=next(row for row in matrix if row['blocker']=='RAW_CLOSE_BASIS_EVIDENCE_MISSING')
    assert raw['if_fixed_total_scores_possible_upper_bound'] == 1
    assert raw['real_external_evidence_required'] is True
