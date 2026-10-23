# Managing the Arxiv deluge
*(Totally using this as an excuse to make a readme.)*

It can be overwhelming to try and stay on top of all the material being released daily. 

To manage this, I've set up a little workflow to **filter all potentially relevant preprints**. 

*(Caveat: it is very much cobbled together and could be streamlined quite a lot so feel free to make a pull request and submit your changes.)*

The folder consists of **2 python files**. 
1. The first one *fetches* papers by making an API call to arxiv.com, and 
2. the second file *displays* them. 

### Set-up

1. We set up a **virtual python environment** for the relevant packages. To this end, use the `requirements.txt` file to create either a virtualenv, conda or otherwise. See [here](https://packaging.python.org/en/latest/guides/installing-using-pip-and-virtual-environments/) or [here](https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html). Mine is called **arxiv** so adjust this in your .sh file accordingly. 

2. To save time and do everything at once, I've created `move-fetch-display.sh` which does all tasks at once. For your purposes:

    a. **Adapt** the `.sh` file, line 4, to fit your venv.

    b. Make the `.sh` file **executible** by running 
```
chmod +x move-fetch-display.sh
```
It can now be executed, see below, which will
    
*   a. activate the virtualenv, 
*  b. check if there's a current `.db` file then rename and move it to `past-papers/` with the creation date
* c. make an api call and then 
* d. serve it all locally. 

3. Make the past-papers folder using 
I store previous results in a folder which I'll consolidate at some future point. To this end:

```
mkdir past-papers
```

4.  **Tailor** the `config.json` file to your own needs by adjusting the following: 
- *days_back* : How many days into the past to look up
- *categories* : Which categories in the arxiv taxonomy to query. 
- *keywords* : Reads the abstract and extracts keywords you're interested in 
- *authors* : Matches author surnames -- if there are specific academics (like your supervisor, or friends) whose new preprints you don't want to miss. (Note, you would have to also be following the right arxiv categories)

### Execution
And finally, run the file by using
```
./move-fetch-display.sh
```


