function stats = strength_stats(levels)
% STRENGTH_STATS summary of password strength levels from the nightly report
stats.mean = mean(levels);
stats.weak_share = sum(levels == 0) / numel(levels);
end
